"""Snapshot-bound display-only attribute edits; never change text or nets.

Reference: easyeda-api-skill 1.1.29 SCH_PrimitiveAttribute.modify.
The caller journals each intent and stops after an uncertain write.
"""
import json

from .drawing_api import _IDENTITY, VERIFIED_EDITOR_VERSION, validate_snapshot
from .drawing_source import records


def validate_visibility(snapshot, attribute_id, visible):
    validate_snapshot(snapshot)
    rows = records(snapshot['source'])
    if type(visible) is not bool:
        raise ValueError('可见性必须为布尔值')
    if attribute_id not in rows:
        raise ValueError('显示属性不存在')
    env, attr = rows[attribute_id]
    parent = rows.get(attr.get('parentId'))
    if (env['type'] != 'ATTR' or attr.get('key') not in ('Name', 'NET', 'Global Net Name')
            or not parent or parent[0]['type'] not in ('COMPONENT', 'WIRE')
            or not isinstance(attr.get('value'), str) or not attr['value']):
        raise ValueError('仅允许已有器件/导线的名称或网络文字显示属性')
    if attr.get('valueVisible') is visible:
        raise ValueError('显示属性已符合目标')


def visibility_script(snapshot, attribute_id, visible):
    validate_visibility(snapshot, attribute_id, visible)
    payload = dict(identity=snapshot['identity'], source=snapshot['source'],
                   id=attribute_id, visible=visible, editor_version=VERIFIED_EDITOR_VERSION)
    return 'const input=JSON.parse(' + json.dumps(json.dumps(payload, ensure_ascii=True)) + ');' + _IDENTITY + r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('显示修改前源变化');
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本未核验');
 const item=await eda.sch_PrimitiveAttribute.get(input.id);
 if(!item||item.getState_PrimitiveId()!==input.id)throw new Error('显示属性缺失');
 const last=await stable(input.identity);
 if(comparable(last.source)!==comparable(input.source))throw new Error('显示提交前源变化');
 attempted=true;
 if(!await eda.sch_PrimitiveAttribute.modify(input.id,{valueVisible:input.visible}))throw new Error('显示修改无返回');
 return {status:'readback',attempted,...await stable(input.identity)};
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,error:String(error).slice(0,2000)};}
"""


def verify_visibility(before, after, attribute_id, visible):
    validate_visibility(before, attribute_id, visible)
    validate_snapshot(after)
    if before['identity'] != after['identity']:
        raise ValueError('显示修改期间目标变化')
    old, new = records(before['source']), records(after['source'])
    if set(old) != set(new):
        raise ValueError('显示修改不得增加或删除图元')
    for rid, (env, value) in old.items():
        new_env, new_value = new[rid]
        ignored = {'zIndex'} if env['type'] == 'ATTR' else set()
        if rid == attribute_id:
            ignored.add('valueVisible')
        if (env != new_env or {k:v for k,v in value.items() if k not in ignored} !=
                {k:v for k,v in new_value.items() if k not in ignored}):
            raise ValueError('显示修改存在方案外差异：' + rid)
    if new[attribute_id][1].get('valueVisible') is not visible:
        raise ValueError('显示修改未生效')
    return {'attribute_id':attribute_id, 'visible':visible,
            'geometry_values_and_nets_unchanged':True}
