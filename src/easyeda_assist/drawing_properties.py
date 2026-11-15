"""Bounded schematic metadata repair; no geometry, device or net edits.

The caller must journal a snapshot-bound repair and retain the existing target
lock. Only values on existing, explicitly named component ATTR records change.
API signatures: easyeda-api-skill 1.1.29 SCH_PrimitiveComponent.modify/get.
"""
from __future__ import annotations

import json

from .drawing_api import _IDENTITY, VERIFIED_EDITOR_VERSION, validate_snapshot
from .drawing_source import records

ALLOWED = {"Supplier Part", "Value", "Voltage Rating", "Datasheet", "Name"}


def frame_components_script(snapshot, primitive_ids):
    """Change viewport only, fitting actual component origins with margin."""
    validate_snapshot(snapshot)
    rows = records(snapshot["source"])
    if (not isinstance(primitive_ids, list) or not primitive_ids or len(primitive_ids)>50 or
            len(set(primitive_ids)) != len(primitive_ids) or
            any(pid not in rows or rows[pid][0]["type"] != "COMPONENT" for pid in primitive_ids)):
        raise ValueError("视图目标须为快照中明确的器件")
    payload = {"identity":snapshot["identity"],"source":snapshot["source"],"ids":primitive_ids}
    return "const input=JSON.parse("+json.dumps(json.dumps(payload,ensure_ascii=True))+");"+_IDENTITY+r"""
const before=await stable(input.identity);
if(comparable(before.source)!==comparable(input.source))throw new Error('视图定位前文档变化');
const points=[];
for(const pid of input.ids){
 const item=await eda.sch_PrimitiveComponent.get(pid);
 if(!item||item.getState_PrimitiveId()!==pid)throw new Error('视图目标器件缺失');
 points.push([item.getState_X(),item.getState_Y()]);
}
const bounds=[Math.min(...points.map(p=>p[0]))-120,Math.max(...points.map(p=>p[0]))+120,
 Math.min(...points.map(p=>p[1]))-120,Math.max(...points.map(p=>p[1]))+120];
const zoomed=await eda.dmt_EditorControl.zoomToRegion(...bounds,input.identity.tab_id);
const after=await stable(input.identity);
if(comparable(before.source)!==comparable(after.source))throw new Error('视图定位期间文档变化');
return {zoomed,bounds,identity:after.identity,source:after.source};
"""


def validate_patch(snapshot, primitive_id, patch):
    validate_snapshot(snapshot)
    rows = records(snapshot["source"])
    if primitive_id not in rows or rows[primitive_id][0]["type"] != "COMPONENT":
        raise ValueError("目标必须是已有器件")
    if not isinstance(patch, dict) or not patch or not set(patch) <= ALLOWED:
        raise ValueError("仅允许明确列出的显示/BOM属性")
    attrs = {}
    for _, (env, value) in rows.items():
        if env["type"] == "ATTR" and value.get("parentId") == primitive_id:
            key = value.get("key")
            if key in attrs:
                raise ValueError("器件属性重复")
            attrs[key] = value.get("value")
    for key, value in patch.items():
        if key not in attrs or not isinstance(value, str) or not value or len(value) > 1000:
            raise ValueError("只能修复已存在的属性，且值必须是有限非空文本")
    if all(attrs[k] == v for k, v in patch.items()):
        raise ValueError("属性已符合目标，无需写入")
    return attrs


_QUERY = _IDENTITY + r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('文档源已变化');
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本尚未核验');
 const item=await eda.sch_PrimitiveComponent.get(input.primitive_id);
 if(!item||item.getState_ComponentType()!=='part'||item.getState_PrimitiveId()!==input.primitive_id||item.getState_Designator()!==input.designator)throw new Error('器件身份不符');
 const property={},other={...(item.getState_OtherProperty()||{})};
 let hasOther=false;
 for(const [key,value] of Object.entries(input.patch)){
  if(key==='Supplier Part')property.supplierId=value;
  else if(key==='Name')property.name=value;
  else {other[key]=value;hasOther=true;}
 }
 if(hasOther)property.otherProperty=other;
 const lastBefore=await stable(input.identity);
 if(comparable(lastBefore.source)!==comparable(input.source))throw new Error('写入前文档源变化');
 attempted=true;
 const changed=await eda.sch_PrimitiveComponent.modify(input.primitive_id,property);
 if(!changed)throw new Error('属性修改无返回，状态未知');
 let previous=null,equal=0;
 for(let i=0;i<16;i++){
  const current=await eda.sch_PrimitiveComponent.get(input.primitive_id);
  if(!current||current.getState_PrimitiveId()!==input.primitive_id)throw new Error('修改后器件不可读');
  const otherNow=current.getState_OtherProperty()||{};
  const observed={};
  for(const key of Object.keys(input.patch))observed[key]=key==='Supplier Part'?current.getState_SupplierId():key==='Name'?current.getState_Name():otherNow[key];
  const state=await stable(input.identity);
  const fingerprint=JSON.stringify([observed,comparable(state.source)]);
  equal=fingerprint===previous?equal+1:0;previous=fingerprint;
  if(equal>=2&&Object.entries(input.patch).every(([k,v])=>observed[k]===v))return {status:'verified',attempted,primitive_id:input.primitive_id,observed,...state};
  await new Promise(resolve=>setTimeout(resolve,250));
 }
 throw new Error('属性回读未稳定或与计划不一致');
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,primitive_id:input.primitive_id,error:String(error).slice(0,2000)};}
"""


def property_script(snapshot, primitive_id, patch):
    attrs = validate_patch(snapshot, primitive_id, patch)
    payload = {"identity": snapshot["identity"], "source": snapshot["source"],
               "editor_version": VERIFIED_EDITOR_VERSION, "primitive_id": primitive_id,
               "designator": attrs["Designator"], "patch": patch}
    literal = json.dumps(json.dumps(payload, ensure_ascii=True, allow_nan=False))
    return "const input=JSON.parse(" + literal + ");" + _QUERY


def verify_property_patch(before, after, primitive_id, patch):
    validate_patch(before, primitive_id, patch)
    validate_snapshot(after)
    if before["identity"] != after["identity"]:
        raise ValueError("属性修复期间目标变化")
    old, new = records(before["source"]), records(after["source"])
    if set(old) != set(new):
        raise ValueError("属性修复不允许新增或删除记录")
    changed = []
    seen = {}
    for rid, (env, value) in new.items():
        old_env, old_value = old[rid]
        if env["type"] == "ATTR" and value.get("parentId") == primitive_id:
            seen[value.get("key")] = value.get("value")
        if old[rid] == new[rid]:
            continue
        key = value.get("key")
        if (env != old_env or env["type"] != "ATTR" or
                value.get("parentId") != primitive_id or key not in patch or
                {k:v for k,v in value.items() if k != "value"} !=
                {k:v for k,v in old_value.items() if k != "value"} or value.get("value") != patch[key]):
            raise ValueError("属性修复出现方案外源差异：" + rid)
        changed.append(rid)
    if any(seen.get(k) != v for k,v in patch.items()) or not changed:
        raise ValueError("属性源回读与目标不一致")
    return {"changed_attribute_ids": changed, "geometry_and_other_records_unchanged": True}
