"""Bounded additive schematic circuit contract, independent Python readback.

Uses only APIs documented in easyeda-api-skill 1.1.29. The gateway never
receives caller code. New library imports are proved *after* create by DEVICE
META.source, rather than guessing the editor's generated project UUID.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
import zipfile
from pathlib import Path

from .drawing_api import ROTATION_CONVENTION, VERIFIED_EDITOR_VERSION, _IDENTITY, comparable_source, validate_snapshot
from .drawing_library import verify_mapping
from .drawing_source import records, verify_addition, verify_wire_merge, wire_segments
from .storage import digest, external, read_json

CONTRACT = "schematic-circuit-v3"
OPERATIONS = {"place_component", "add_wire", "place_net_flag"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1000000


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and bool(value.strip()) and not any(ord(c) < 32 for c in value)


def library_evidence(parameters: dict) -> tuple[dict, dict]:
    evidence = parameters["library_evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"path", "sha256"}:
        raise ValueError("library_evidence 必须含路径和 SHA256")
    path = Path(evidence["path"])
    if not path.is_absolute() or not re.fullmatch("[0-9a-f]{64}", evidence["sha256"]):
        raise ValueError("库证据路径或哈希无效")
    path = external(path)
    if path.stat().st_size > 16 * 1024 * 1024 or digest(path) != evidence["sha256"]:
        raise ValueError("库证据过大或哈希不一致")
    record = read_json(path)
    return record, inspect_library(record, parameters)


def inspect_library(record: dict, p: dict) -> dict:
    if any(record.get(k) != p[k] for k in ("library_uuid", "device_uuid")):
        raise ValueError("库归档器件身份不一致")
    encoded = record.get("archive_base64")
    if not isinstance(encoded, str) or len(encoded) > 14 * 1024 * 1024:
        raise ValueError("库归档缺失或过大")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 10 * 1024 * 1024 or record.get("file_size") != len(raw):
            raise ValueError("库归档大小不一致")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            logs = [m for m in members if m.filename.endswith(".elibu")]
            if len(members) > 1000 or len({m.filename for m in members}) != len(members) or len(logs) != 1 or sum(m.file_size for m in members) > 30 * 1024 * 1024:
                raise ValueError("库归档结构无效或解压后过大")
            info = json.loads(archive.read("device2.json"))
            log = archive.read(logs[0]).decode("utf-8-sig")
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError) as exc:
        raise ValueError("无法解析 elibz2 库归档") from exc
    device = info["devices"][p["device_uuid"]]
    symbol_uuid = device["attributes"]["Symbol"]
    if device["path"] != p["library_uuid"] or device.get("symbol_type") != 2:
        raise ValueError("只接受来源明确的普通器件")
    # Resolve library log winners (ticket then smaller client), including repeat headers.
    winners, header = {}, None
    for line in log.splitlines():
        outer, sep, inner = line.partition("||")
        if not sep:
            raise ValueError("库日志格式无效")
        outer = json.loads(outer)
        value = json.loads(inner.rstrip().removesuffix("|"))
        if outer.get("type") == "DOCHEAD":
            header = value
            continue
        if not header or header.get("uuid") != symbol_uuid or header.get("docType") != "SYMBOL":
            continue
        if not isinstance(value, dict) or outer.get("type", "").startswith("DELETE"):
            raise ValueError("库符号含删除或未知记录")
        key = outer["id"]
        order = (outer["ticket"], outer.get("client", header.get("client", "")))
        old = winners.get(key)
        if old and order == old[0] and (outer["type"], value) != old[1:]:
            raise ValueError("库符号日志同序冲突")
        if not old or order[0] > old[0][0] or (order[0] == old[0][0] and order[1] < old[0][1]):
            winners[key] = (order, outer["type"], value)
    parts = [(rid, v) for rid, (_, kind, v) in winners.items() if kind == "PART" and v.get("title", "") == p["sub_part_name"]]
    if len(parts) != 1:
        raise ValueError("库子部件名称不唯一或不存在")
    part_id, part = parts[0]
    pins = []
    # The editor rotates the primitive opposite to its API rotation, while pin
    # coordinates follow the API rotation. This was confirmed against live C1.
    pin_rotation = p.get("requested_api_rotation", p["expected_canvas_rotation"])
    c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[pin_rotation]
    def transform(x, y):
        if not _number(x) or not _number(y):
            raise ValueError("库符号坐标无效")
        return p["x"] + c*x - s*y, p["y"] - (s*x + c*y)
    for rid, (_, kind, v) in winners.items():
        if kind != "PIN" or v.get("partId") != part_id:
            continue
        numbers = [a.get("value") for _, k, a in winners.values()
                   if k == "ATTR" and a.get("parentId") == rid and a.get("key") in {"Pin Number", "NUMBER"}]
        if len(numbers) != 1:
            raise ValueError("库引脚编号属性缺失或存在歧义")
        number = numbers[0]
        if not _text(number):
            raise ValueError("库引脚缺少编号")
        x, y = transform(v["x"], v["y"])
        pins.append({"number": number, "x": x, "y": y})
    if not 1 <= len(pins) <= 500 or len({pin["number"] for pin in pins}) != len(pins):
        raise ValueError("库引脚为空、过多或编号重复")
    if sorted(pins, key=lambda pin: pin["number"]) != sorted(p["expected_pins"], key=lambda pin: pin["number"]):
        raise ValueError("计划引脚与库符号变换后坐标不一致")
    box = part.get("BBOX")
    if not isinstance(box, list) or len(box) != 4 or not all(_number(v) for v in box):
        raise ValueError("库符号缺少可验证边界")
    corners = [transform(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
    bounds = [min(x for x,y in corners), min(y for x,y in corners), max(x for x,y in corners), max(y for x,y in corners)]
    return {"symbol_uuid": symbol_uuid, "footprint_uuid": device["attributes"].get("Footprint"),
            "pins": pins, "bounds": bounds, "attributes": device["attributes"]}


def validate_operation(op: dict) -> None:
    kind, p = op["operation"], op["parameters"]
    common = {"x", "y", "unit", "requested_api_rotation", "expected_canvas_rotation", "mirror", "rotation_convention"}
    if kind == "place_component":
        fields = common | {"library_uuid", "device_uuid", "library_evidence", "expected_pins", "sub_part_name", "add_into_bom", "add_into_pcb"}
        if "designator" in p:
            fields.add("designator")
    elif kind == "place_net_flag":
        fields = common | {"identification", "net"}
    elif kind == "add_wire":
        fields = {"unit", "net", "line"}
    else:
        raise ValueError("未实现的电路操作")
    if not isinstance(p, dict) or set(p) != fields or p["unit"] != "10mil":
        raise ValueError("操作 parameters 字段或单位不符合固定契约：" + kind)
    if kind == "add_wire":
        wire_segments(p["line"])
    else:
        if not all(_number(p[k]) for k in ("x", "y")):
            raise ValueError("器件坐标无效")
        if any(type(p[k]) is not int or p[k] not in (0,90,180,270) for k in ("requested_api_rotation", "expected_canvas_rotation")) or p["expected_canvas_rotation"] != (-p["requested_api_rotation"]) % 360 or p["rotation_convention"] != ROTATION_CONVENTION:
            raise ValueError("旋转约定不一致")
        if p["mirror"] is not False:
            raise ValueError("v3 暂不支持镜像，须先独立核验")
    if kind in {"add_wire", "place_net_flag"} and not _text(p["net"]):
        raise ValueError("网络名无效")
    if kind == "place_net_flag" and p["identification"] not in {"Power", "Ground", "AnalogGround", "ProtectGround"}:
        raise ValueError("网络标号类型无效")
    if kind == "place_component":
        if any(not _text(p[k]) or "|" in p[k] for k in ("library_uuid", "device_uuid")) or not isinstance(p["sub_part_name"], str):
            raise ValueError("库身份或子部件名称无效")
        if "designator" in p and (not _text(p["designator"]) or not re.fullmatch(r"[A-Za-z]+[0-9]+", p["designator"])):
            raise ValueError("指定位号无效")
        if any(type(p[k]) is not bool for k in ("add_into_bom", "add_into_pcb")):
            raise ValueError("BOM/PCB 标记须为布尔值")
        pins = p["expected_pins"]
        if not isinstance(pins, list) or not 1 <= len(pins) <= 500 or any(not isinstance(pin, dict) or set(pin) != {"number", "x", "y"} or not _text(pin["number"]) or not all(_number(pin[k]) for k in ("x", "y")) for pin in pins) or len({pin["number"] for pin in pins}) != len(pins):
            raise ValueError("预期引脚必须完整且编号唯一")


def point_on(point, segment):
    axis, fixed, a, b = segment
    x, y = point
    # Source serialization can emit 294.99999999999994 for a 295-unit pin.
    # This is floating point noise, not a schematic-grid snapping tolerance.
    eps = 1e-9
    return (abs(y-fixed) <= eps and a-eps <= x <= b+eps) if axis == "H" else (abs(x-fixed) <= eps and a-eps <= y <= b+eps)


def baseline_wire_nets(source_records, wire_id, source_line):
    """Resolve only explicit NETs and global flags physically on this wire."""
    nets = {v.get("value") for e,v in source_records.values()
            if e.get("type") == "ATTR" and v.get("parentId") == wire_id
            and v.get("key", "").upper() == "NET"}
    segments = wire_segments(source_line)
    for env, value in source_records.values():
        if env.get("type") != "ATTR" or value.get("key") != "Global Net Name":
            continue
        parent = source_records.get(value.get("parentId"))
        if parent and parent[0].get("type") == "COMPONENT":
            x, y = parent[1].get("x"), parent[1].get("y")
            if _number(x) and _number(y) and any(point_on((x,-y), s) for s in segments):
                nets.add(value.get("value"))
    if any(not _text(net) for net in nets):
        raise ValueError("基线导线网络属性无效")
    return nets


def intersects(a, b):
    if a[0] == b[0]:
        return a[1] == b[1] and max(a[2],b[2]) <= min(a[3],b[3])
    h, v = (a,b) if a[0] == "H" else (b,a)
    return h[2] <= v[1] <= h[3] and v[2] <= h[1] <= v[3]


def validate_circuit(plan: dict, snapshot: dict) -> dict:
    source_records = records(snapshot["source"])
    components = {rid for rid,(env,_) in source_records.items() if env.get("type") == "COMPONENT"}
    borders = {value.get("parentId") for env,value in source_records.values() if env.get("type") == "ATTR" and value.get("key") == "Border" and value.get("value") == "1"}
    base_wires = plan.get("base_wires", [])
    if not isinstance(base_wires, list) or len(base_wires) > 50:
        raise ValueError("base_wires 必须是受限基线导线数组")
    wire_ids = {rid for rid, (env, _) in source_records.items() if env.get("type") == "WIRE"}
    declared_wires = {item.get("primitive_id") for item in base_wires if isinstance(item, dict)}
    if wire_ids != declared_wires or any(env.get("type") in {"BUS", "BUSENTRY"} for env,_ in source_records.values()):
        raise ValueError("现场导线必须精确匹配声明的恢复基线")
    for item in base_wires:
        if set(item) != {"primitive_id", "net", "line"} or not _text(item["primitive_id"]) or not _text(item["net"]):
            raise ValueError("基线导线字段无效")
        wire_segments(item["line"])
        primitive = source_records[item["primitive_id"]][1]
        if primitive.get("dots") is not None:
            source_line = primitive["dots"]
        else:
            source_line = [[v["startX"], -v["startY"], v["endX"], -v["endY"]]
                           for e, v in source_records.values()
                           if e.get("type") == "LINE" and v.get("lineGroup") == item["primitive_id"]]
        if wire_segments(source_line) != wire_segments(item["line"]):
            raise ValueError("基线导线几何不一致")
        if baseline_wire_nets(source_records, item["primitive_id"], source_line) != {item["net"]}:
            raise ValueError("基线导线网络名与声明不一致")
    if plan.get("execution_contract") != CONTRACT:
        raise ValueError("电路计划缺少 v3 契约")
    operations = plan["operations"]
    baseline = plan.get("base_components", [])
    base_flags = plan.get("base_flags", [])
    if not isinstance(baseline, list) or len(baseline) > 20:
        raise ValueError("base_components 必须是受限基线器件数组")
    if not isinstance(base_flags, list) or len(base_flags) > 20:
        raise ValueError("base_flags 必须是受限基线网络标号数组")
    geometries = {}
    allowed = {str(item.get("primitive_id")): item for item in baseline if isinstance(item, dict)}
    if len(allowed) != len(baseline):
        raise ValueError("base_components 图元 ID 重复或格式无效")
    actual_parts = components - borders
    flag_map = {item.get("primitive_id"): item for item in base_flags if isinstance(item, dict)}
    if len(flag_map) != len(base_flags) or actual_parts != set(allowed) | set(flag_map):
        raise ValueError("现场器件必须精确匹配声明的恢复基线")
    for rid, item in allowed.items():
        env, value = source_records[rid]
        attrs = {v.get("key"): v for e, v in source_records.values()
                 if e.get("type") == "ATTR" and v.get("parentId") == rid}
        if (item.get("primitive_id") != rid or item.get("x") != value.get("x") or
                item.get("y") != -value.get("y") or
                item.get("designator") != attrs.get("Designator", {}).get("value") or
                item.get("project_device_uuid") != attrs.get("Device", {}).get("value")):
            raise ValueError("恢复基线器件身份、位置、位号或工程来源不一致")
        required = {"primitive_id", "designator", "project_device_uuid", "library_uuid", "device_uuid",
                    "x", "y", "requested_api_rotation", "expected_canvas_rotation", "expected_pins", "sub_part_name", "library_evidence"}
        if set(item) != required or not _text(item["designator"]):
            raise ValueError("恢复基线字段不符合固定契约")
        geometry = library_evidence({**item, "unit": "10mil"})[1]
        if sorted(geometry["pins"], key=lambda pin: pin["number"]) != sorted(item["expected_pins"], key=lambda pin: pin["number"]):
            raise ValueError("恢复基线引脚与库归档不一致")
        b = geometry["bounds"]
        if b[0] < 0 or b[1] < 0 or b[2] > 1170 or b[3] > 825:
            raise ValueError("基线器件外形超出 A4 图框")
        geometries["base:" + rid] = geometry
    for rid, item in flag_map.items():
        value = source_records[rid][1]
        attrs = {v.get("key"): v for e, v in source_records.values()
                 if e.get("type") == "ATTR" and v.get("parentId") == rid}
        required = {"primitive_id", "net", "x", "y", "project_device_uuid", "library_uuid", "device_uuid"}
        if (set(item) != required or item["primitive_id"] != rid or
                item["x"] != value.get("x") or item["y"] != -value.get("y") or
                item["net"] != attrs.get("Global Net Name", {}).get("value") or
                item["project_device_uuid"] != attrs.get("Device", {}).get("value")):
            raise ValueError("基线网络标号身份、位置或网络名不一致")
    for op in operations:
        p = op.get("parameters", {})
        if op.get("operation") in {"place_component", "place_net_flag"} and not (
                0 <= p.get("x", -1) <= 1170 and 0 <= p.get("y", -1) <= 825):
            raise ValueError("新增对象坐标超出 A4 图框")
    nets, flags = {}, []
    for item in base_wires:
        primitive = source_records[item["primitive_id"]][1]
        dots = primitive.get("dots")
        if dots is None:
            dots = [[v["startX"], -v["startY"], v["endX"], -v["endY"]]
                    for e, v in source_records.values()
                    if e.get("type") == "LINE" and v.get("lineGroup") == item["primitive_id"]]
        nets.setdefault(item["net"], []).extend(wire_segments(dots))
    stage = 0
    for op in operations:
        validate_operation(op)
        p, kind = op["parameters"], op["operation"]
        if kind == "place_component":
            if stage:
                raise ValueError("先放置所有器件，再放置标号和导线")
            geometries[op["id"]] = library_evidence(p)[1]
        elif kind == "add_wire":
            stage = 2
            segments = wire_segments(p["line"])
            reached = {0}
            while True:
                new = reached | {i for i, a in enumerate(segments) if any(intersects(a,segments[j]) for j in reached)}
                if new == reached:
                    break
                reached = new
            if len(reached) != len(segments):
                raise ValueError("同一导线操作含不连通分支")
            nets.setdefault(p["net"], []).extend(segments)
        else:
            if stage == 2:
                raise ValueError("网络标号须在导线之前放置")
            stage = 1
            flags.append(p)
    connectivity = plan.get("connectivity")
    if not isinstance(connectivity, list) or not connectivity:
        raise ValueError("v3 必须声明每个新增器件引脚的最终网络")
    expected = {}
    for item in connectivity:
        if not isinstance(item, dict) or set(item) != {"component", "pin", "net"} or not all(_text(item[k]) for k in item):
            raise ValueError("connectivity 条目无效")
        key = (item["component"], item["pin"])
        if key in expected:
            raise ValueError("引脚连接声明重复")
        expected[key] = item["net"]
    # Base component IDs are primitive IDs; operation IDs identify additions.
    normalized_pins = {}
    for cid, g in geometries.items():
        actual_id = cid[5:] if cid.startswith("base:") else cid
        for pin in g["pins"]:
            normalized_pins[(actual_id, pin["number"])] = pin
    if set(expected) != set(normalized_pins):
        raise ValueError("必须精确覆盖基线与新增器件的全部引脚")
    all_pins = normalized_pins
    for key, pin in all_pins.items():
        touched = {net for net, segments in nets.items() if any(point_on((pin["x"],pin["y"]), s) for s in segments)}
        touched |= {flag["net"] for flag in flags if (flag["x"], flag["y"]) == (pin["x"], pin["y"])}
        touched |= {flag["net"] for flag in base_flags if (flag["x"], flag["y"]) == (pin["x"], pin["y"])}
        if touched != {expected[key]}:
            raise ValueError("计划导线没有精确落在指定网络引脚：" + str(key))
    for flag in flags:
        touched = {net for net,segs in nets.items() if any(point_on((flag["x"],flag["y"]),s) for s in segs)}
        touched |= {expected[key] for key, pin in all_pins.items()
                    if (pin["x"], pin["y"]) == (flag["x"], flag["y"])}
        if flag["net"] not in nets or touched != {flag["net"]}:
            raise ValueError("网络标号必须落在对应网络导线或声明的器件引脚上")
    for flag in base_flags:
        touched = {net for net, segs in nets.items() if any(point_on((flag["x"], flag["y"]), s) for s in segs)}
        touched |= {expected[key] for key, pin in all_pins.items()
                    if (pin["x"], pin["y"]) == (flag["x"], flag["y"])}
        if flag["net"] not in nets or touched != {flag["net"]}:
            raise ValueError("基线网络标号必须连到其声明网络")
    for net, segments in nets.items():
        for other, others in nets.items():
            if net < other and any(intersects(a,b) for a in segments for b in others):
                raise ValueError("不同网络导线交叉或重合；v3 要求分开走线")
    for cid, g in geometries.items():
        a = g["bounds"]
        if a[0] < 0 or a[1] < 0 or a[2] > 1170 or a[3] > 825:
            raise ValueError("器件外形超出 A4 图框")
        for other, h in geometries.items():
            b = h["bounds"]
            if cid < other and max(a[0],b[0]) < min(a[2],b[2]) and max(a[1],b[1]) < min(a[3],b[3]):
                raise ValueError("计划器件本体相互重叠")
    return {"library_geometry": geometries, "connectivity": connectivity}


_QUERY = _IDENTITY + r"""
let attempted=false, primitiveId=null;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('文档源已变化');
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本尚未核验');
 const p=input.parameters, kind=input.operation;
 if(!same(await identity(),input.identity))throw new Error('写入前目标变化');
 attempted=true;
 let created;
 if(kind==='place_component')created=await eda.sch_PrimitiveComponent.create(
   {libraryUuid:p.library_uuid,uuid:p.device_uuid},p.x,p.y,p.sub_part_name,p.requested_api_rotation,false,p.add_into_bom,p.add_into_pcb);
 else if(kind==='add_wire')created=await eda.sch_PrimitiveWire.create(p.line,p.net);
 else if(kind==='place_net_flag')created=await eda.sch_PrimitiveComponent.createNetFlag(p.identification,p.net,p.x,p.y,p.requested_api_rotation,false);
 else throw new Error('不支持的固定操作');
 if(!created)throw new Error('创建无返回，写入状态未知');
 primitiveId=created.getState_PrimitiveId();
 if(typeof primitiveId!=='string'||!primitiveId.trim())throw new Error('创建没有有效图元ID');
 if(kind==='place_component'&&p.designator){
  const modified=await eda.sch_PrimitiveComponent.modify(primitiveId,{designator:p.designator});
  if(!modified)throw new Error('设置器件位号无返回，写入状态未知');
 }
 // Only read-side settling; never retry a write. Require three matching reads
 // across >=500ms and a fully assigned designator on ordinary components.
 let previous=null, equal=0, last=null;
 for(let attempt=0;attempt<12;attempt++){
  if(!same(await identity(),input.identity))throw new Error('写入后目标变化');
  const item=kind==='add_wire'?await eda.sch_PrimitiveWire.get(primitiveId):await eda.sch_PrimitiveComponent.get(primitiveId);
  if(!item||item.getState_PrimitiveId()!==primitiveId)throw new Error('无法回读创建图元');
  let observed,pins=[];
  if(kind==='add_wire')observed={primitive_id:primitiveId,net:item.getState_Net(),line:item.getState_Line()};
  else{
   observed={primitive_id:primitiveId,x:item.getState_X(),y:item.getState_Y(),rotation:item.getState_Rotation(),mirror:item.getState_Mirror(),
    component_type:item.getState_ComponentType(),net:item.getState_Net()??null};
   if(kind==='place_component'){
    const device=item.getState_Component();
    Object.assign(observed,{library_uuid:device?.libraryUuid,device_uuid:device?.uuid,sub_part_name:item.getState_SubPartName()??null,
     designator:item.getState_Designator()??null,add_into_bom:item.getState_AddIntoBom(),add_into_pcb:item.getState_AddIntoPcb()});
   }
   const objects=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(primitiveId);
   if(!Array.isArray(objects)||objects.length>500)throw new Error('引脚回读不兼容');
   pins=objects.map(pin=>({primitive_id:pin.getState_PrimitiveId(),number:pin.getState_PinNumber(),x:pin.getState_X(),y:pin.getState_Y()}));
  }
  const state=await stable(input.identity);
  last={observed,pins,...state};
  const fingerprint=JSON.stringify({...last,source:comparable(last.source)});
  equal=fingerprint===previous?equal+1:0;previous=fingerprint;
  const ready=kind!=='place_component'||(typeof observed.designator==='string'&&/^[A-Za-z]+[0-9]+$/.test(observed.designator)&&(p.designator===undefined||observed.designator===p.designator));
  if(equal>=2&&ready)return {status:'verified',attempted,primitive_id:primitiveId,...last};
  await new Promise(resolve=>setTimeout(resolve,250));
 }
 throw new Error('创建后的源或属性未在限定读取时间内稳定');
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,primitive_id:primitiveId,error:String(error).slice(0,2000)};}
"""


_AUDIT_QUERY = _IDENTITY + r"""
const before=await stable(input.identity);
if(comparable(before.source)!==comparable(input.source))throw new Error('文档源已变化');
const file=await eda.sch_ManufactureData.getNetlistFile('design-assist','JLCEDA');
if(!file||!Number.isFinite(file.size)||file.size>20971520)throw new Error('网表缺失或超过20MiB');
const raw=await file.text();
if(raw.length>20971520)throw new Error('网表超过20MiB');
const errors=await eda.sch_Drc.check(true,false,true);
if(!Array.isArray(errors)||errors.length>5000||errors.some(e=>!e||typeof e!=='object'||!['fatalError','error','warn'].includes(e.type)))throw new Error('DRC返回格式不兼容');
const after=await stable(input.identity);
if(comparable(before.source)!==comparable(after.source))throw new Error('审计期间文档源变化');
return {identity:after.identity,source:after.source,raw_netlist:raw,
 drc:errors,drc_passed:errors.length===0};
"""


_RELOCATE_QUERY = _IDENTITY + r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('文档源已变化');
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本尚未核验');
 const item=await eda.sch_PrimitiveComponent.get(input.primitive_id);
 if(!item||item.getState_PrimitiveId()!==input.primitive_id||item.getState_ComponentType()!=='part')throw new Error('指定器件不存在');
 const device=item.getState_Component();
 if(device?.libraryUuid!==input.library_uuid||device?.uuid!==input.project_device_uuid||
  item.getState_X()!==input.from.x||item.getState_Y()!==input.from.y||item.getState_Designator()!==input.from.designator)
  throw new Error('器件身份或当前位置与恢复记录不一致');
 if(!same(await identity(),input.identity))throw new Error('写入前目标变化');
 attempted=true;
 const updated=await eda.sch_PrimitiveComponent.modify(input.primitive_id,{x:input.to.x,y:input.to.y,designator:input.to.designator});
 if(!updated)throw new Error('修改接口无返回，状态未知');
 let previous=null,equal=0,last=null;
 for(let attempt=0;attempt<12;attempt++){
  if(!same(await identity(),input.identity))throw new Error('修改后目标变化');
  const current=await eda.sch_PrimitiveComponent.get(input.primitive_id);
  if(!current||current.getState_PrimitiveId()!==input.primitive_id)throw new Error('无法回读修改图元');
  last={primitive_id:input.primitive_id,library_uuid:current.getState_Component()?.libraryUuid,
   device_uuid:current.getState_Component()?.uuid,x:current.getState_X(),y:current.getState_Y(),
   designator:current.getState_Designator(),rotation:current.getState_Rotation(),mirror:current.getState_Mirror()};
  const state=await stable(input.identity);last={...last,...state};
  const fingerprint=JSON.stringify({...last,source:comparable(last.source)});
  equal=fingerprint===previous?equal+1:0;previous=fingerprint;
  if(equal>=2&&last.x===input.to.x&&last.y===input.to.y&&last.designator===input.to.designator)
   return {status:'verified',attempted,...last};
  await new Promise(resolve=>setTimeout(resolve,250));
 }
 throw new Error('移动后的位置、位号或文档源未稳定');
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,primitive_id:input.primitive_id,error:String(error).slice(0,2000)};}
"""


def relocate_component_script(snapshot: dict, primitive_id: str, project_device_uuid: str,
                              from_state: dict, to_state: dict) -> str:
    validate_snapshot(snapshot)
    if (not _text(primitive_id) or not _text(project_device_uuid) or
            not isinstance(from_state, dict) or set(from_state) != {"x", "y", "designator"} or
            not isinstance(to_state, dict) or set(to_state) != {"x", "y", "designator"} or
            not all(_number(state[k]) for state in (from_state, to_state) for k in ("x", "y")) or
            not all(_text(state["designator"]) for state in (from_state, to_state)) or
            from_state == to_state):
        raise ValueError("器件恢复位置参数无效")
    payload = {"identity": snapshot["identity"], "source": snapshot["source"],
               "editor_version": VERIFIED_EDITOR_VERSION, "primitive_id": primitive_id,
               "project_device_uuid": project_device_uuid, "library_uuid": "0819f05c4eef4c71ace90d822a990e87",
               "from": from_state, "to": to_state}
    literal = json.dumps(json.dumps(payload, ensure_ascii=True, allow_nan=False), ensure_ascii=True)
    return "const input=JSON.parse(" + literal + ");" + _RELOCATE_QUERY


def verify_relocation(reply: dict, before: dict, primitive_id: str,
                      from_state: dict, to_state: dict) -> dict:
    if (not isinstance(reply, dict) or reply.get("status") != "verified" or
            reply.get("attempted") is not True or reply.get("primitive_id") != primitive_id):
        raise ValueError("器件位置恢复未通过实时回读")
    if any(reply.get(key) != value for key, value in to_state.items()):
        raise ValueError("器件恢复后位置或位号不一致")
    after = {**before, "identity": reply.get("identity"), "source": reply.get("source")}
    validate_snapshot(after)
    if after["identity"] != before["identity"]:
        raise ValueError("器件恢复后目标变化")
    old, new = records(before["source"]), records(after["source"])
    if set(old) - set(new):
        raise ValueError("器件恢复期间删除了已有图元")
    added = set(new) - set(old)
    if len(added) > 100 or any(new[rid][0].get("type") != "ATTR" or
                               new[rid][1].get("parentId") != primitive_id for rid in added):
        raise ValueError("器件恢复期间新增了非目标器件属性")
    changed = {rid for rid in old if old[rid] != new[rid]}
    if primitive_id not in changed or len(changed) > 20:
        raise ValueError("器件恢复源差异范围无效")
    primitive = new[primitive_id][1]
    if primitive.get("x") != to_state["x"] or primitive.get("y") != -to_state["y"]:
        raise ValueError("器件文档源位置与目标坐标不一致")
    designators = [value for _, value in new.values()
                   if value.get("parentId") == primitive_id and value.get("key") == "Designator"]
    if len(designators) != 1 or designators[0].get("value") != to_state["designator"]:
        raise ValueError("器件源位号与恢复目标不一致")
    for rid in changed - {primitive_id}:
        old_env, old_value = old[rid]
        new_env, new_value = new[rid]
        if old_env.get("type") != "ATTR" or new_env != old_env or old_value.get("parentId") != primitive_id:
            raise ValueError("器件恢复修改了非目标属性")
        key = old_value.get("key")
        allowed = {"x", "y", "value"}
        if any(old_value.get(field) != new_value.get(field)
               for field in set(old_value) | set(new_value) if field not in allowed):
            raise ValueError("器件恢复修改了未授权属性：" + str(key))
        if key == "Designator" and new_value.get("value") != to_state["designator"]:
            raise ValueError("器件源位号与恢复目标不一致")
    return after


def audit_script(snapshot: dict) -> str:
    validate_snapshot(snapshot)
    payload = {"identity": snapshot["identity"], "source": snapshot["source"]}
    literal = json.dumps(json.dumps(payload, ensure_ascii=True, allow_nan=False), ensure_ascii=True)
    return "const input=JSON.parse(" + literal + ");" + _AUDIT_QUERY


def audit_circuit(reply: dict, snapshot: dict, plan: dict) -> dict:
    from .schematic import components_from_raw
    if not isinstance(reply, dict) or reply.get("identity") != snapshot["identity"]:
        raise ValueError("电路审计目标身份不一致")
    after = {**snapshot, "source": reply.get("source")}
    validate_snapshot(after)
    if comparable_source(after["source"]) != comparable_source(snapshot["source"]):
        raise ValueError("电路审计期间文档源变化")
    components = components_from_raw(reply.get("raw_netlist"))
    observed = {}
    designators = set()
    for component in components.values():
        props = component["props"]
        designator = props.get("Designator")
        if not _text(designator) or designator in designators:
            raise ValueError("网表器件位号缺失或重复")
        designators.add(designator)
        for pin in component["pinInfoMap"].values():
            if isinstance(designator, str) and isinstance(pin.get("number"), str):
                key = (designator, pin["number"])
                if key in observed:
                    raise ValueError("网表引脚编号重复")
                observed[key] = pin.get("net", "")
    additions = {op["id"]: op["parameters"] for op in plan["operations"] if op["operation"] == "place_component"}
    # Designators are read from the final netlist at runtime through source identity;
    # callers must provide the resulting operation->designator correspondence.
    mapping = plan.get("result_designators")
    if not isinstance(mapping, dict) or set(mapping) != set(additions):
        raise ValueError("审计缺少器件操作与最终位号对应关系")
    expected = {}
    baseline_designators = {item["primitive_id"]: item["designator"] for item in plan.get("base_components", [])}
    for item in plan["connectivity"]:
        opid, number = item["component"], item["pin"]
        designator = mapping.get(opid, baseline_designators.get(opid))
        if not _text(designator):
            raise ValueError("审计位号无效")
        key = (designator, number)
        if key in expected:
            raise ValueError("计划映射产生重复位号引脚")
        expected[key] = item["net"]
    mismatches = [{"designator": d, "pin": n, "expected_net": net, "observed_net": observed.get((d,n))}
                  for (d,n),net in expected.items() if (d,n) not in observed or observed[(d,n)] != net]
    baseline = plan.get("base_components", [])
    for item in baseline:
        for pin in item["expected_pins"]:
            expected[(item["designator"], pin["number"])] = next(
                net for entry in plan["connectivity"]
                if entry["component"] == item["primitive_id"] and entry["pin"] == pin["number"]
                for net in (entry["net"],))
    if set(observed) != set(expected) or len(components) != len(additions) + len(baseline):
        raise ValueError("网表包含遗漏或方案外器件/引脚；当前电路不符合恢复基线")
    drc = reply.get("drc")
    if (not isinstance(drc, list) or len(drc) > 5000 or
            any(not isinstance(item, dict) or item.get("type") not in {"fatalError", "error", "warn"} for item in drc) or
            type(reply.get("drc_passed")) is not bool or reply["drc_passed"] != (len(drc) == 0)):
        raise ValueError("DRC审计字段无效")
    return {"component_count": len(components), "pin_count": len(observed), "netlist_sha256": __import__('hashlib').sha256(reply["raw_netlist"].encode()).hexdigest(),
            "connectivity_matches": not mismatches, "connectivity_mismatches": mismatches,
            "drc_count": len(reply["drc"]) if isinstance(reply.get("drc"), list) else None, "drc": reply.get("drc"), "drc_passed": reply["drc_passed"],
            "limitations": ["网表只证明 API 导出连接；DRC 不等于电气完整性/板级验证。", "DRC 可能包含原有工程问题。"]}


def operation_script(snapshot: dict, op: dict) -> str:
    validate_snapshot(snapshot)
    validate_operation(op)
    payload = {"identity": snapshot["identity"], "source": snapshot["source"], "editor_version": VERIFIED_EDITOR_VERSION,
               "operation": op["operation"], "parameters": op["parameters"]}
    literal = json.dumps(json.dumps(payload, ensure_ascii=True, allow_nan=False), ensure_ascii=True)
    return "const input=JSON.parse(" + literal + ");" + _QUERY


def verify_reply(reply: dict, before: dict, op: dict) -> dict:
    p, kind = op["parameters"], op["operation"]
    if reply.get("status") != "verified" or reply.get("attempted") is not True:
        raise ValueError("操作未通过回读")
    pid, observed = reply.get("primitive_id"), reply.get("observed")
    if not _text(pid) or not isinstance(observed, dict) or observed.get("primitive_id") != pid:
        raise ValueError("回读图元身份无效")
    after = {**before, "source": reply.get("source"), "identity": reply.get("identity")}
    validate_snapshot(after)
    if after["identity"] != before["identity"]:
        raise ValueError("写入后目标变化")
    old_ids = records(before["source"])
    if kind == "add_wire" and pid in old_ids:
        delta = verify_wire_merge(before["source"], after["source"], pid, p["line"], p["net"])
    else:
        delta = verify_addition(before["source"], after["source"], pid, "WIRE" if kind == "add_wire" else "COMPONENT")
    def check(key, value):
        if key not in observed or type(observed[key]) is not type(value) and not (_number(value) and _number(observed[key])) or observed[key] != value:
            raise ValueError("回读字段不一致：" + key)
    if kind == "add_wire":
        check("net", p["net"])
        source_records = records(after["source"])
        line_ids = set(delta["added_ids"]) if pid in old_ids else None
        line_values = [value for rid, (env, value) in source_records.items()
                       if env.get("type") == "LINE" and value.get("lineGroup") == pid and
                       (line_ids is None or rid in line_ids)]
        dots = delta["primitive"].get("dots")
        if dots is not None:
            wire_segments(dots)
            groups = [dots] if not isinstance(dots[0], list) else dots
            api_dots = [[-v if i % 2 else v for i, v in enumerate(group)] for group in groups]
        elif line_values:
            api_dots = [[line["startX"], -line["startY"], line["endX"], -line["endY"]]
                        for line in line_values]
            wire_segments(api_dots)
        else:
            raise ValueError("导线源缺少可核验的线段图元")
        if wire_segments(api_dots) != wire_segments(p["line"]):
            raise ValueError("导线回读几何不一致")
        attrs = delta["attributes"]
        if not any(a.get("value") == p["net"] and k.upper() == "NET" for k,a in attrs.items()):
            raise ValueError("导线源缺少指定网络属性")
    else:
        for key in ("x", "y", "mirror"):
            check(key,p[key])
        check("rotation",p["expected_canvas_rotation"])
        check("component_type", "part" if kind == "place_component" else "netflag")
        primitive = delta["primitive"]
        for key in ("x","y","rotation"):
            wanted = p["expected_canvas_rotation"] if key == "rotation" else (-p[key] if key == "y" else p[key])
            if primitive.get(key) != wanted or not _number(primitive.get(key)):
                raise ValueError("器件源与回读坐标/角度不一致")
        if primitive.get("isMirror") is not False:
            raise ValueError("器件源镜像状态不一致")
        pins = reply.get("pins")
        if not isinstance(pins, list) or any(not isinstance(pin,dict) or not _text(pin.get("primitive_id")) or not _text(pin.get("number")) or not _number(pin.get("x")) or not _number(pin.get("y")) for pin in pins) or len({pin["primitive_id"] for pin in pins}) != len(pins):
            raise ValueError("引脚回读字段无效")
        if kind == "place_net_flag":
            check("net",p["net"])
            if len(pins) != 1 or pins[0]["x"] != p["x"] or pins[0]["y"] != p["y"]:
                raise ValueError("网络标号连接点与计划不一致")
        else:
            for key in ("library_uuid","sub_part_name","add_into_bom","add_into_pcb"):
                check(key,p[key])
            if not _text(observed.get("device_uuid")) or not isinstance(observed.get("designator"),str) or not re.fullmatch(r"[A-Za-z]+[0-9]+",observed["designator"]):
                raise ValueError("器件工程身份或位号尚未稳定")
            if p.get("designator") is not None and observed["designator"] != p["designator"]:
                raise ValueError("器件位号与计划不一致")
            if len(pins) != len(p["expected_pins"]) or sorted(({k:pin[k] for k in ("number","x","y")} for pin in pins),key=lambda v:v["number"]) != sorted(p["expected_pins"],key=lambda v:v["number"]):
                raise ValueError("器件引脚坐标不一致")
            attrs = delta["attributes"]
            if attrs.get("Device",{}).get("value") != observed["device_uuid"] or attrs.get("Designator",{}).get("value") != observed["designator"]:
                raise ValueError("器件源身份/位号与回读不一致")
            verify_mapping(reply.get("project_evidence"),after,{**p,"expected_project_device_uuid":observed["device_uuid"]})
    return after
