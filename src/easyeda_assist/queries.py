"""One fixed read service for analysis, operation preparation and verification."""
from __future__ import annotations

import hashlib
import json

from .drawing_api import _IDENTITY, comparable_source
from .contracts import validate_context
from .storage import now

QUERY_NAMES = ("context", "snapshot", "components", "pins", "connectivity", "source-component", "source-groups", "audit")

_SOURCE_TARGET = r"""
async function sourceTarget(expected){
 const schematic=await eda.dmt_Schematic.getCurrentSchematicInfo();
 const page=await eda.dmt_Schematic.getCurrentSchematicPageInfo();
 if(!schematic||!page||page.uuid!==expected.document_uuid||page.parentSchematicUuid!==schematic.uuid||
    typeof schematic.name!=='string'||!schematic.name||typeof page.name!=='string'||!page.name)
  throw new Error('Source名称与焦点页UUID映射不一致');
 return {projectId:expected.project_uuid,schematicName:schematic.name,pageName:page.name};
}
"""


def source_query_script(kind, primitive_id=None):
    if kind not in ("source-component", "source-groups"):
        raise ValueError("未知Source读取")
    if kind == "source-component" and (not isinstance(primitive_id, str) or not primitive_id.strip()):
        raise ValueError("source-component 需要明确 primitive_id")
    payload = json.dumps(json.dumps({"primitive_id": primitive_id}, ensure_ascii=True))
    body = r"""
const before=await stable();
const target=await sourceTarget(before.identity);
const outputs={};
const read=async fn=>{try{const result=await fn();return result?.ok===true?
 {status:'returned',value:result.value}:{status:'unavailable',raw:result??null};}
 catch(error){return {status:'error',error:String(error).slice(0,2000)};}};
"""
    if kind == "source-component":
        body += "outputs.component=await read(()=>eda.sch_SourcePrimitiveComponent.get({...target,primitiveId:input.primitive_id}));\n"
        body += "outputs.pin_geometry=await read(()=>eda.sch_SourcePin.getGeometry({...target,primitiveId:input.primitive_id}));\n"
    else:
        body += "outputs.function_groups=await read(()=>eda.sch_SourceDocument.getFunctionGroups(target));\n"
    body += r"""
const after=await stable(before.identity);
if(!same(target,await sourceTarget(after.identity))||comparable(before.source)!==comparable(after.source))
 throw new Error('Source读取期间目标、名称或源发生变化');
return {identity:after.identity,target,editor_version:eda.sys_Environment.getEditorCurrentVersion(),
 source:after.source,outputs,primitive_id:input.primitive_id};
"""
    return "const input=JSON.parse(" + payload + ");" + _IDENTITY + _SOURCE_TARGET + body


def read_query(bridge, kind, window_id=None, primitive_id=None):
    if kind not in QUERY_NAMES:
        raise ValueError("未知固定查询")
    if kind in ("pins", "source-component") and (not isinstance(primitive_id, str) or not primitive_id.strip()):
        raise ValueError("此查询需要 primitive_id")
    if kind == "audit":
        from .design_audit import make_audit
        from .drawing_circuit import audit_script
        snapshot = bridge.drawing_snapshot(window_id)
        script_hash = hashlib.sha256(audit_script(snapshot).encode()).hexdigest()
        data = make_audit(snapshot, bridge.drawing_circuit_audit(snapshot), script_hash)
        method, missing = data["provenance"]["method"], ["derived_freshness"]
    elif kind in ("source-component", "source-groups"):
        script = source_query_script(kind, primitive_id)
        data, window = bridge._read_fixed(script, window_id)
        if not isinstance(data.get("identity"), dict) or not isinstance(data.get("target"), dict):
            raise ValueError("Source查询缺少目标身份")
        if data["target"].get("projectId") != data["identity"].get("project_uuid"):
            raise ValueError("Source查询目标工程不一致")
        from .drawing_api import validate_snapshot
        validate_snapshot({"schema_version": 1, "kind": "drawing-source", "captured_at": now(),
                           "domain": "schematic", "window_id": window, **data})
        outputs = data.get("outputs")
        keys = {"component", "pin_geometry"} if kind == "source-component" else {"function_groups"}
        if not isinstance(outputs, dict) or set(outputs) != keys or any(
            not isinstance(item, dict) or item.get("status") not in {"returned", "unavailable", "error"}
            for item in outputs.values()
        ):
            raise ValueError("Source查询返回结构不兼容")
        if kind == "source-component" and outputs["component"]["status"] == "returned":
            value = outputs["component"].get("value")
            if not isinstance(value, dict) or value.get("primitiveId") != primitive_id:
                raise ValueError("Source返回了不同的器件身份")
        data = {**data, "window_id": window}
        method = "sch_SourcePrimitiveComponent.get / sch_SourcePin.getGeometry" if kind == "source-component" else "sch_SourceDocument.getFunctionGroups"
        script_hash = hashlib.sha256(script.encode()).hexdigest()
        missing = [key for key, item in outputs.items() if item["status"] != "returned"]
    else:
        readers = {"context": bridge.capture, "snapshot": bridge.drawing_snapshot,
                   "components": bridge.drawing_components, "connectivity": bridge.schematic}
        data = bridge.drawing_pins(primitive_id, window_id) if kind == "pins" else readers[kind](window_id)
        method, script_hash, missing = kind, None, []
        if kind == "context":
            validate_context(data)
        if data.get("truncated") is True:
            missing.append("truncated")
        if kind == "context" and data.get("selection_details_status") not in (None, "captured"):
            missing.append("selection_details")
    if not data.get("editor_version"):
        missing.append("editor_version")
    return {"schema_version": 1, "kind": "design-query", "query": kind, "captured_at": now(),
            "target": {"window_id": data.get("window_id"),
                       "identity": data.get("identity") or {"project": data.get("project"), "document": data.get("document")},
                       "source_target": data.get("target")},
            "provenance": {"method": method, "editor_version": data.get("editor_version"),
                           "query_sha256": script_hash, "transport": "bridge"},
            "missing_fields": missing, "data": data,
            "limitations": ["各查询独立采集，不能跨快照按同名网络合并。",
                            "派生功能分组只提供线索；未知电气方向保持未知。",
                            "Source读取限定打开页；原始返回保留，不证明工程正确性。"]}
