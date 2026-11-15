"""Remove one redundant GND symbol while retaining an explicit wire name.

This is a journalled repair primitive, not a general delete endpoint. Callers
must compare the native pin/net map before and after each operation.
API reference: easyeda-api-skill 1.1.29 SCH_PrimitiveComponent.delete/get.
"""
import json
import math

from .drawing_api import _IDENTITY, VERIFIED_EDITOR_VERSION, validate_snapshot
from .drawing_source import records


def removal_parameters(snapshot, flag_id, wire_id):
    validate_snapshot(snapshot)
    rows = records(snapshot['source'])
    if flag_id not in rows or rows[flag_id][0]['type'] != 'COMPONENT':
        raise ValueError('目标必须是已有网络标志')
    if wire_id not in rows or rows[wire_id][0]['type'] != 'WIRE':
        raise ValueError('必须提供保留的已有导线')
    attrs = {v['key']: v for e, v in rows.values()
             if e['type'] == 'ATTR' and v.get('parentId') == flag_id}
    if (attrs.get('Global Net Name', {}).get('value') != 'GND'
            or attrs.get('Designator', {}).get('value')
            or attrs.get('Add into BOM', {}).get('value') != 'no'
            or attrs.get('Convert to PCB', {}).get('value') != 'no'):
        raise ValueError('仅允许非BOM/PCB的目标网络标志')
    names = [v for e, v in rows.values() if e['type'] == 'ATTR'
             and v.get('parentId') == wire_id and v.get('key') == 'NET']
    if len(names) != 1 or names[0].get('value') != 'GND' or names[0].get('valueVisible') is not True:
        raise ValueError('保留导线必须有可见且唯一的目标网络名称')
    xy = rows[flag_id][1]
    x, y = xy.get('x'), xy.get('y')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)):
        raise ValueError('标志坐标无效')
    attached = any(e['type'] == 'LINE' and v.get('lineGroup') == wire_id
                  and any(abs(x - v[a]) < 1e-8 and abs(y - v[b]) < 1e-8
                          for a, b in (('startX','startY'), ('endX','endY')))
                  for e, v in rows.values())
    if not attached:
        raise ValueError('标志必须连接到所保留网络导线端点')
    if not any(e['type'] == 'ATTR' and v.get('key') == 'Global Net Name'
               and v.get('value') == 'GND' and v.get('parentId') != flag_id
               for e, v in rows.values()):
        raise ValueError('不能删除最后一个目标网络全局标志')
    return dict(identity=snapshot['identity'], source=snapshot['source'],
                flag_id=flag_id, wire_id=wire_id, editor_version=VERIFIED_EDITOR_VERSION)


def remove_redundant_ground_script(snapshot, flag_id, wire_id):
    payload = removal_parameters(snapshot, flag_id, wire_id)
    return 'const input=JSON.parse(' + json.dumps(json.dumps(payload, ensure_ascii=True)) + ');' + _IDENTITY + r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('地符号简化前源变化');
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本未核验');
 const flag=await eda.sch_PrimitiveComponent.get(input.flag_id);
 if(!flag||flag.getState_ComponentType()!=='netflag'||flag.getState_PrimitiveId()!==input.flag_id)throw new Error('目标不是网络标志');
 const last=await stable(input.identity);
 if(comparable(last.source)!==comparable(input.source))throw new Error('地符号提交前源变化');
 attempted=true;
 if(!await eda.sch_PrimitiveComponent.delete(input.flag_id))throw new Error('删除无成功返回，须独立回读');
 return {status:'readback',attempted,...await stable(input.identity)};
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,error:String(error).slice(0,2000)};}
"""


def verify_ground_removal(before, after, flag_id, wire_id):
    removal_parameters(before, flag_id, wire_id)
    validate_snapshot(after)
    if before['identity'] != after['identity']:
        raise ValueError('地符号简化期间目标变化')
    old, new = records(before['source']), records(after['source'])
    deleted = {flag_id} | {rid for rid, (e, v) in old.items()
                          if e['type'] == 'ATTR' and v.get('parentId') == flag_id}
    if set(old) - set(new) != deleted or set(new) - set(old):
        raise ValueError('只能删除目标地符号及其直属属性')
    removed_tickets = [old[rid][0].get('ticket') for rid in deleted]
    renumbered = any(old[rid][0] != new[rid][0] for rid in new)
    if renumbered and any(type(t) is not int for t in removed_tickets):
        raise ValueError('无法验证删除后的ticket压缩')
    for rid, (e, v) in new.items():
        oe, ov = old[rid]
        expected_env = dict(oe)
        if renumbered and 'ticket' in oe:
            expected_env['ticket'] = oe['ticket'] - sum(t < oe['ticket'] for t in removed_tickets)
        ignored = {'zIndex'} if e['type'] == 'ATTR' else set()
        if e != expected_env or {k:x for k,x in v.items() if k not in ignored} != {k:x for k,x in ov.items() if k not in ignored}:
            raise ValueError('地符号简化出现方案外差异：' + rid)
    return {'deleted_ids': sorted(deleted), 'all_wires_parts_and_remaining_values_unchanged': True}
