"""Experimental Source adapter; closed until version-specific live validation.

Signatures: saved 4.1.100 sys_Help, SourceComponent.update (incremental
properties), SourceComponent.get, DMT_Schematic current-page identity getters.
Only existing whitelisted values change; the strict source delta is reused.
"""
from __future__ import annotations

import json

from .drawing_api import _IDENTITY, comparable_source, validate_snapshot
from .drawing_properties import ALLOWED, validate_patch, verify_property_patch
from .drawing_source import records
from .operations import ADAPTERS, SOURCE, require_dispatch
from .queries import _SOURCE_TARGET


def validate_parameters(parameters):
    if not isinstance(parameters, dict) or set(parameters) != {"primitive_id", "patch", "source_target"}:
        raise ValueError("Source属性参数必须精确为 primitive_id、patch、source_target")
    if not isinstance(parameters["primitive_id"], str) or not parameters["primitive_id"].strip():
        raise ValueError("Source属性缺少图元身份")
    target = parameters["source_target"]
    if not isinstance(target, dict) or set(target) != {"projectId", "schematicName", "pageName"} or any(
        not isinstance(v, str) or not v.strip() for v in target.values()
    ):
        raise ValueError("Source目标必须包含明确的工程ID、原理图名、页名")
    patch = parameters["patch"]
    if not isinstance(patch, dict) or not patch or not set(patch) <= ALLOWED or any(
        not isinstance(v, str) or not v or len(v) > 1000 for v in patch.values()
    ):
        raise ValueError("Source属性仅接受已有白名单字段的有限非空值")


def prepare(snapshot, parameters):
    validate_parameters(parameters)
    if parameters["source_target"]["projectId"] != snapshot["identity"]["project_uuid"]:
        raise ValueError("Source目标与快照工程不一致")
    return validate_patch(snapshot, parameters["primitive_id"], parameters["patch"])


def operation_script(snapshot, parameters):
    prepare(snapshot, parameters)
    require_dispatch(SOURCE)
    return _fixed_property_script(snapshot, parameters)


def _fixed_property_script(snapshot, parameters, backend="source"):
    """Shared fixed implementation; experimental trials enforce their own binding."""
    attrs = prepare(snapshot, parameters)
    if backend not in {"source", "canvas-attribute"}:
        raise ValueError("未知实验属性后端")
    payload = {"identity": snapshot["identity"], "source": snapshot["source"],
               "editor_version": ADAPTERS[SOURCE]["editor_version"], "backend": backend, **parameters}
    precheck = ""
    submit = "const reply=await eda.sch_SourcePrimitiveComponent.update({...options,properties:input.patch});"
    if backend == "canvas-attribute":
        if len(parameters["patch"]) != 1:
            raise ValueError("画布属性实验一次仅允许一个已有属性")
        key = next(iter(parameters["patch"]))
        candidates = [(rid, value) for rid, (env, value) in records(snapshot["source"]).items()
                      if env["type"] == "ATTR" and value.get("parentId") == parameters["primitive_id"] and value.get("key") == key]
        if len(candidates) != 1:
            raise ValueError("属性图元必须唯一")
        payload["attribute"] = {"id": candidates[0][0], "key": key, "before": attrs[key]}
        precheck = r"""
 const attr=await eda.sch_PrimitiveAttribute.get(input.attribute.id);
 if(!attr||attr.getState_PrimitiveId()!==input.attribute.id||attr.getState_ParentPrimitiveId()!==input.primitive_id||
    attr.getState_Key()!==input.attribute.key||attr.getState_Value()!==input.attribute.before)
  throw new Error('画布属性身份或原值不符');
"""
        submit = r"""
 const changed=await eda.sch_PrimitiveAttribute.modify(input.attribute.id,{value:input.patch[input.attribute.key]});
 if(!changed)throw new Error('画布属性修改未确认');
 const reply={ok:true,value:{primitiveId:input.primitive_id,attributeId:input.attribute.id},backend:input.backend};
"""
    body = r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('写入前完整源已变化');
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本未核验');
 const target=await sourceTarget(before.identity);
 if(!same(target,input.source_target))throw new Error('Source名称映射已变化');
 const options={...target,primitiveId:input.primitive_id};
 const original=await eda.sch_SourcePrimitiveComponent.get(options);
 if(original?.ok!==true||original.value?.primitiveId!==input.primitive_id)throw new Error('Source器件身份无法核对');
 __PRECHECK__
 const finalBefore=await stable(input.identity);
 if(comparable(finalBefore.source)!==comparable(input.source)||!same(target,await sourceTarget(finalBefore.identity)))
  throw new Error('提交前目标或源变化');
 attempted=true;
 __SUBMIT__
 if(reply?.ok!==true)throw new Error('Source写入未确认，保留未知：'+JSON.stringify(reply));
 let previous=null,consecutive=0;
 const deadline=Date.now()+6000;
 for(let i=0;i<24&&Date.now()<deadline;i++){
  const observed=await eda.sch_SourcePrimitiveComponent.get(options);
  const identityBefore=await identity();
  if(!same(identityBefore,input.identity)||!same(target,await sourceTarget(identityBefore)))throw new Error('回读期间目标变化');
  const currentSource=await source();
  if(!same(await identity(),input.identity))throw new Error('回读期间焦点变化');
  const ready=observed?.ok===true&&observed.value?.primitiveId===input.primitive_id&&
   Object.entries(input.patch).every(([k,v])=>observed.value.properties?.[k]===v)&&
   comparable(currentSource)!==comparable(input.source);
  const fingerprint=ready?JSON.stringify([observed.value,comparable(currentSource)]):null;
  consecutive=ready&&fingerprint===previous?consecutive+1:ready?1:0;previous=fingerprint;
  if(consecutive>=3)return {status:'verified',attempted,primitive_id:input.primitive_id,
   identity:identityBefore,source:currentSource,source_target:target,
   original:original.value,observed:observed.value,api_reply:reply,stable_reads:consecutive,backend:input.backend};
  await new Promise(resolve=>setTimeout(resolve,250));
 }
 throw new Error('Source预期状态未在期限内稳定；写入不重试');
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,primitive_id:input.primitive_id,error:String(error).slice(0,2000),backend:input.backend};}
"""
    return ("const input=JSON.parse(" + json.dumps(json.dumps(payload, ensure_ascii=True)) + ");" + _IDENTITY + _SOURCE_TARGET +
            body.replace("__PRECHECK__", precheck).replace("__SUBMIT__", submit))


def verify_reply(reply, before, parameters):
    prepare(before, parameters)
    if reply.get("status") != "verified" or reply.get("attempted") is not True:
        raise ValueError("Source写入没有完成稳定回读")
    if reply.get("primitive_id") != parameters["primitive_id"] or reply.get("source_target") != parameters["source_target"]:
        raise ValueError("Source写入回读对象或名称映射不同")
    if type(reply.get("stable_reads")) is not int or reply["stable_reads"] < 3:
        raise ValueError("Source回读缺少连续稳定证据")
    original, observed = reply.get("original"), reply.get("observed")
    if not isinstance(original, dict) or not isinstance(observed, dict):
        raise ValueError("Source回读缺少完整器件状态")
    if original.get("primitiveId") != parameters["primitive_id"] or observed.get("primitiveId") != parameters["primitive_id"]:
        raise ValueError("Source器件详情身份不符")
    if {k: v for k, v in original.items() if k != "properties"} != {k: v for k, v in observed.items() if k != "properties"}:
        raise ValueError("Source器件几何、引脚或身份出现方案外变化")
    properties = original.get("properties")
    if not isinstance(properties, dict) or observed.get("properties") != {**properties, **parameters["patch"]}:
        raise ValueError("Source属性没有完整保留或值不符")
    after = {**before, "identity": reply.get("identity"), "source": reply.get("source")}
    validate_snapshot(after)
    verify_property_patch(before, after, parameters["primitive_id"], parameters["patch"])
    if comparable_source(before["source"]) == comparable_source(after["source"]):
        raise ValueError("Source回读仍是写入前快照")
    return after
