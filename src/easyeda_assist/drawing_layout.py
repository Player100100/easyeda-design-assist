"""Snapshot-bound, single-object schematic layout operations.

No source import, deletion, net rename, device change or free-form code input.
Callers journal intent before each call and stop on any uncertain result.
API reference: easyeda-api-skill 1.1.29 ISCH_PrimitiveComponent.toAsync,
setState_X/Y/done, SCH_PrimitiveWire.modify, SCH_PrimitiveAttribute.modify.
"""
from __future__ import annotations

import json
import math

from .drawing_api import _IDENTITY, VERIFIED_EDITOR_VERSION, validate_snapshot
from .drawing_source import records, wire_segments


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) < 1000000


def parameters(snapshot, primitive_id, dx, dy):
    validate_snapshot(snapshot)
    rows = records(snapshot['source'])
    if not finite(dx) or not finite(dy) or (dx == 0 and dy == 0):
        raise ValueError('位移必须为有限非零数值')
    if primitive_id not in rows or rows[primitive_id][0]['type'] not in ('COMPONENT', 'WIRE', 'ATTR'):
        raise ValueError('只允许已有器件、导线和显示属性')
    env, value = rows[primitive_id]
    kind = env['type']
    if kind == 'ATTR' and (value.get('key') not in ('Name', 'Designator', 'NET', 'Global Net Name') or
                           not value.get('parentId')):
        raise ValueError('只允许移动已有显示文字')
    if kind != 'WIRE' and (not finite(value.get('x')) or not finite(value.get('y'))):
        raise ValueError('缺少可移动的显式坐标')
    if kind == 'COMPONENT' and not any(e['type']=='ATTR' and v.get('parentId')==primitive_id and v.get('key') in ('Designator','Global Net Name') and v.get('value') for e,v in rows.values()):
        raise ValueError('不能移动图框等非器件对象')
    payload = dict(identity=snapshot['identity'], source=snapshot['source'],
                   editor_version=VERIFIED_EDITOR_VERSION, primitive_id=primitive_id,
                   kind=kind, dx=dx, dy=dy)
    attrs={v['key']:v for e,v in rows.values() if e['type']=='ATTR' and v.get('parentId')==primitive_id}
    if kind=='COMPONENT' and 'Global Net Name' in attrs:
        net=attrs['Global Net Name'].get('value')
        if not isinstance(net,str) or not net:raise ValueError('网络标志的网名必须先恢复')
        payload['flag_values']={k:attrs[k].get('value') for k in ('Name','Global Net Name')}
        payload['flag_values'].update({'Add into BOM':'no','Convert to PCB':'no'})
    elif kind=='COMPONENT':
        supplier=attrs.get('Supplier Part',{}).get('value')
        if not isinstance(supplier,str) or not supplier:raise ValueError('器件 Supplier Part 缺失，拒绝几何修改')
        payload['supplier_id']=supplier
        payload['preserve_properties']={k:attrs[k]['value'] for k in ('Value','Voltage Rating','Datasheet') if k in attrs and isinstance(attrs[k].get('value'),str)}
    if kind == 'WIRE':
        payload['net']=next((v.get('value') for e,v in rows.values() if e['type']=='ATTR' and v.get('parentId')==primitive_id and v.get('key','').upper()=='NET'),None)
        if not isinstance(payload['net'],str) or not payload['net']:raise ValueError('导线网络名缺失')
        payload['line'] = [[v['startX'] + dx, -v['startY'] + dy,
                            v['endX'] + dx, -v['endY'] + dy]
                           for e, v in rows.values() if e['type'] == 'LINE' and v.get('lineGroup') == primitive_id]
        if not payload['line']:
            raise ValueError('导线没有线段')
    else:
        payload.update(x=value['x'] + dx, y=-value['y'] + dy)
    return payload


_TRANSLATE = _IDENTITY + r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('布局写入前文档变化');
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本未核验');
 let item;
 if(input.kind==='COMPONENT')item=await eda.sch_PrimitiveComponent.get(input.primitive_id);
 else if(input.kind==='WIRE')item=await eda.sch_PrimitiveWire.get(input.primitive_id);
 else item=await eda.sch_PrimitiveAttribute.get(input.primitive_id);
 if(!item||item.getState_PrimitiveId()!==input.primitive_id)throw new Error('布局目标缺失');
 const last=await stable(input.identity);
 if(comparable(last.source)!==comparable(input.source))throw new Error('布局提交前文档变化');
 attempted=true;
 if(input.kind==='COMPONENT'){
  if(item.getState_ComponentType()==='part'){
   if(!await eda.sch_PrimitiveComponent.modify(input.primitive_id,{x:input.x,y:input.y,supplierId:input.supplier_id,otherProperty:{...(item.getState_OtherProperty()||{}),...input.preserve_properties}}))throw new Error('器件移动无返回');
  }else if(item.getState_ComponentType()==='netflag'){
   if(!input.flag_values)throw new Error('缺少网络标志原值');
   const changed=item.toSync();
   if(input.dx!==0)changed.setState_X(input.x);
   if(input.dy!==0)changed.setState_Y(input.y);
   const attrs=await eda.sch_PrimitiveAttribute.getAll(input.primitive_id);
   for(const [key,value] of Object.entries(input.flag_values)){
    const attr=attrs.find(a=>a.getState_Key()===key);
    if(!attr)throw new Error('网络标志属性缺失：'+key);
    if(!await eda.sch_PrimitiveAttribute.modify(attr.getState_PrimitiveId(),{value}))throw new Error('网络标志属性恢复失败');
   }
  }else throw new Error('只允许普通器件和网络标志');
 }else if(input.kind==='WIRE'){
  if(!await eda.sch_PrimitiveWire.modify(input.primitive_id,{line:input.line,net:input.net}))throw new Error('导线修改无返回');
 }else{
  if(!await eda.sch_PrimitiveAttribute.modify(input.primitive_id,{x:input.x,y:input.y}))throw new Error('属性移动无返回');
 }
 const after=await stable(input.identity);
 return {status:'readback',attempted,...after};
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,error:String(error).slice(0,2000)};}
"""


def translation_script(snapshot, primitive_id, dx, dy):
    payload = parameters(snapshot, primitive_id, dx, dy)
    if payload['kind'] == 'WIRE':
        raise ValueError('禁止逐线平移：中间接触会触发自动合网；须经独立整组重建预检')
    return 'const input=JSON.parse(' + json.dumps(json.dumps(payload, ensure_ascii=True)) + ');' + _TRANSLATE


def restore_flag_attributes_script(snapshot, primitive_id, net):
    """Recover only the damaged flag's names and accidental BOM/PCB flags."""
    validate_snapshot(snapshot)
    rows = records(snapshot['source'])
    attrs = {v['key']:(rid,v) for rid,(e,v) in rows.items() if e['type']=='ATTR' and v.get('parentId')==primitive_id}
    if not isinstance(net,str) or not net or len(net)>80 or 'Global Net Name' not in attrs:
        raise ValueError('必须是已有网络标志及明确网名')
    patches=[]
    for key,value in (('Name',net),('Global Net Name',net),('Add into BOM','no'),('Convert to PCB','no')):
        rid,v=attrs[key]
        if v.get('value')!=value:patches.append({'id':rid,'value':value})
    payload={'identity':snapshot['identity'],'source':snapshot['source'],'patches':patches}
    return 'const input=JSON.parse('+json.dumps(json.dumps(payload,ensure_ascii=True))+');'+_IDENTITY+r"""
const before=await stable(input.identity);
if(comparable(before.source)!==comparable(input.source))throw new Error('恢复前文档变化');
for(const patch of input.patches){if(!await eda.sch_PrimitiveAttribute.modify(patch.id,{value:patch.value}))throw new Error('属性恢复失败');}
return await stable(input.identity);
"""


def restore_wire_net_script(snapshot, primitive_id, net):
    validate_snapshot(snapshot)
    rows=records(snapshot['source'])
    if primitive_id not in rows or rows[primitive_id][0]['type']!='WIRE' or not isinstance(net,str) or not net or len(net)>80:
        raise ValueError('必须指定位号线图元和原始网名')
    payload={'identity':snapshot['identity'],'source':snapshot['source'],'id':primitive_id,'net':net}
    return 'const input=JSON.parse('+json.dumps(json.dumps(payload,ensure_ascii=True))+');'+_IDENTITY+r"""
const before=await stable(input.identity);if(comparable(before.source)!==comparable(input.source))throw new Error('导线修复前文档变化');
if(!await eda.sch_PrimitiveWire.modify(input.id,{net:input.net}))throw new Error('导线网络恢复失败');
return await stable(input.identity);
"""


def verify_translation(before, after, primitive_id, dx, dy):
    p = parameters(before, primitive_id, dx, dy)
    validate_snapshot(after)
    if before['identity'] != after['identity']:
        raise ValueError('布局操作期间文档变化')
    old, new = records(before['source']), records(after['source'])
    kind = p['kind']
    def owned_line(record):
        e, v = record
        return e['type'] == 'LINE' and v.get('lineGroup') == primitive_id
    flag=p.get('flag_values')
    flag_extra={'Add into BOM':'no','Convert to PCB':'no','Designator':None,'Unique ID':None,'Manufacturer':None,'Manufacturer Part':None,'Supplier':None,'Supplier Part':None}
    if flag:
        if set(old)-set(new):raise ValueError('网络标志移动不得删除记录')
        for rid in set(new)-set(old):
            e,v=new[rid]
            if e['type']!='ATTR' or v.get('parentId')!=primitive_id or v.get('key') not in flag_extra or v.get('value')!=flag_extra[v['key']] or v.get('x') is not None or v.get('y') is not None:
                raise ValueError('网络标志移动出现未预期新增属性')
    elif kind != 'WIRE' and set(old) != set(new):
        raise ValueError('器件/属性移动不能新增或删除记录')
    if kind == 'WIRE':
        for rid in set(old) ^ set(new):
            if not owned_line((old if rid in old else new)[rid]):
                raise ValueError('导线移动影响了其他对象')
        actual = [[v['startX'], -v['startY'], v['endX'], -v['endY']]
                  for e, v in new.values() if owned_line((e, v))]
        if wire_segments(actual) != wire_segments(p['line']):
            raise ValueError('导线新几何不匹配')
    changed = []
    for rid in set(old) & set(new):
        if old[rid] == new[rid]:
            continue
        changed.append(rid)
        oe, ov = old[rid]; ne, nv = new[rid]
        if oe==ne and oe['type']=='ATTR' and {k:v for k,v in ov.items() if k!='zIndex'}=={k:v for k,v in nv.items() if k!='zIndex'}:
            continue
        if flag and oe==ne and oe['type']=='ATTR' and ov.get('key') in ('Relevance','NET') and ov.get('parentId')!=primitive_id and {k:v for k,v in ov.items() if k!='zIndex'}=={k:v for k,v in nv.items() if k!='zIndex'}:
            continue
        if kind == 'WIRE' and owned_line(old[rid]) and owned_line(new[rid]):
            if oe != ne or {k:v for k,v in ov.items() if k not in ('startX','startY','endX','endY')} != {k:v for k,v in nv.items() if k not in ('startX','startY','endX','endY')}:
                raise ValueError('导线样式发生变化')
            continue
        allowed = rid == primitive_id or (kind in ('COMPONENT','WIRE') and oe['type']=='ATTR' and ov.get('parentId')==primitive_id)
        if not allowed or oe != ne or {k:v for k,v in ov.items() if k not in ('x','y')} != {k:v for k,v in nv.items() if k not in ('x','y')}:
            raise ValueError('布局存在方案外差异：' + rid)
        for key, delta in (('x',dx),('y',-dy)):
            if ov.get(key) != nv.get(key) and (not finite(ov.get(key)) or nv.get(key) != ov[key]+delta):
                raise ValueError('布局坐标变化不匹配：' + rid)
    if kind != 'WIRE' and (new[primitive_id][1].get('x') != p['x'] or new[primitive_id][1].get('y') != -p['y']):
        raise ValueError('目标尚未移动到指定坐标')
    if not changed and set(old)==set(new):
        raise ValueError('布局没有生效')
    return {'changed_records':changed, 'outside_records_unchanged':True}
