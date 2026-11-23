"""Add an isolated circuit on an occupied page without weakening v3 checks.

Existing source records and complete exported components remain immutable.
The region, detailed audit, component inventory and pin geometry are bound to
one baseline. This is additive editing, not arbitrary document replacement.
"""
from __future__ import annotations

from pathlib import Path
import re

from .design_audit import _drc_delta, source_digest, unwrap_audit, make_audit
from .drawing_api import comparable_source, validate_snapshot
from .drawing_circuit import validate_circuit, verify_reply, _number, _text
from .drawing_source import records, wire_segments
from .operations import INCREMENTAL, SOURCE_VERSION, V3
from .drawing_frame import frame_geometry
from .schematic import components_from_raw
from .storage import external, read_json, digest, now

ACTIVE_CLAIM = "incremental-active.json"


def settle_created_component(bridge, snapshot, operation, creation):
    """Allow the creation request to finish before reading lazy editor identity.

    Only fixed export/getters run here. No creation call or setter is retried.
    Keep the initial receipt and each diagnostic, even when settling fails.
    """
    import time
    from .bridge import BridgeError
    from .traceability import canonical_digest
    pid = creation.get("primitive_id")
    if not _text(pid) or creation.get("attempted") is not True or creation.get("window_id") != snapshot["window_id"]:
        return {**creation,"status":"uncertain","error":"创建收据身份不符"}
    result = {**creation,"status":"uncertain","creation_reply":creation,"settling_reads":[]}
    previous,equal = None,0
    for index in range(8):
        if index:
            time.sleep(0.25)
        try:
            # The editor may materialize Unique ID/Designator when an export
            # request returns to its event loop; same-request polling is insufficient.
            audit = bridge.design_query("audit",snapshot["window_id"])
            audit_data = unwrap_audit(audit)
            if audit_data["identity"] != snapshot["identity"] or audit_data["editor_version"] != SOURCE_VERSION:
                result["error"] = "身份补齐期间目标或版本变化"
                return result
            current = bridge.drawing_snapshot(snapshot["window_id"])
            data,window = bridge._read_fixed(inspect_created_script(current,pid),snapshot["window_id"])
            result["settling_reads"].append({"audit":audit,"diagnostic":data,"window_id":window})
            if window != snapshot["window_id"] or data.get("identity") != snapshot["identity"]:
                result["error"] = "身份补齐期间目标变化"
                return result
            o = data["observed"]
            fingerprint = canonical_digest({**data,"source":comparable_source(data["source"])})
            equal = equal+1 if fingerprint == previous else 1
            previous = fingerprint
            ready = (_text(o.get("unique_id")) and isinstance(o.get("designator"),str) and
                     re.fullmatch(r"[A-Za-z]+[0-9]+",o["designator"]))
            if equal >= 3 and ready:
                observed = {k:v for k,v in o.items() if k != "component"}
                observed.update(library_uuid=o["component"]["libraryUuid"],device_uuid=o["component"]["uuid"],component_type="part")
                result.update(status="verified",source=data["source"],identity=data["identity"],observed=observed,pins=data["pins"])
                return result
        except (BridgeError, ValueError, KeyError) as exc:
            result["settling_reads"].append({"error":str(exc)})
            previous,equal = None,0
    result["error"] = "创建请求返回后仍未观察到三次稳定的完整身份"
    return result


def inspect_created_script(snapshot, primitive_id):
    """Fixed diagnostic getters, never creates or modifies an object."""
    import json
    from .drawing_api import _IDENTITY
    validate_snapshot(snapshot)
    if not _text(primitive_id):
        raise ValueError("诊断须指定已有图元")
    payload = json.dumps(json.dumps({"identity":snapshot["identity"],"primitive_id":primitive_id}))
    return "const input=JSON.parse(" + payload + ");" + _IDENTITY + r"""
const before=await stable(input.identity);
const item=await eda.sch_PrimitiveComponent.get(input.primitive_id);
if(!item||item.getState_PrimitiveId()!==input.primitive_id)throw new Error('诊断器件不存在');
const pins=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(input.primitive_id);
const observed={primitive_id:item.getState_PrimitiveId(),component:item.getState_Component(),
 designator:item.getState_Designator()??null,unique_id:item.getState_UniqueId()??null,
 x:item.getState_X(),y:item.getState_Y(),rotation:item.getState_Rotation(),mirror:item.getState_Mirror(),
 sub_part_name:item.getState_SubPartName()??null,add_into_bom:item.getState_AddIntoBom(),add_into_pcb:item.getState_AddIntoPcb()};
const after=await stable(input.identity);
if(comparable(before.source)!==comparable(after.source))throw new Error('诊断读取期间源变化');
return {...after,observed,pins:pins.map(pin=>({primitive_id:pin.getState_PrimitiveId(),number:pin.getState_PinNumber(),x:pin.getState_X(),y:pin.getState_Y()}))};
"""


def claim_lock(lock_dir, run_dir, claim, reviewed_sha256=None):
    from .drawing_execution import _durable_new
    from .source_trial import lock_inventory, ACTIVE_CLAIM as SOURCE_CLAIM
    lock_dir.mkdir(exist_ok=True)
    prior, sha = lock_inventory(lock_dir)
    if any(item["file"] in {ACTIVE_CLAIM, SOURCE_CLAIM} for item in prior):
        raise ValueError("目标已有活动实验，先核对现场")
    if prior and reviewed_sha256 != sha:
        raise ValueError("增量编辑要求先复核完整历史锁清单")
    owned = {"run_dir":str(run_dir), "purpose":claim.get("adapter", INCREMENTAL), **claim}
    _durable_new(lock_dir / ACTIVE_CLAIM, owned)
    _durable_new(run_dir / "incremental-lock-review.json", {"prior":prior,"sha256":sha,"claim":owned})
    verify_lock(lock_dir, owned, prior)
    return owned, prior


def verify_lock(lock_dir, claim, prior):
    from .source_trial import lock_inventory
    files, _ = lock_inventory(lock_dir)
    if (read_json(lock_dir / ACTIVE_CLAIM) != claim or
            [item for item in files if item["file"] != ACTIVE_CLAIM] != prior):
        raise ValueError("增量执行期间目标锁或历史锁发生变化")


def release_lock(lock_dir, claim, prior):
    verify_lock(lock_dir, claim, prior)
    (lock_dir / ACTIVE_CLAIM).unlink()
    if not prior:
        lock_dir.rmdir()


def check_live_baseline(bridge, bundle, snapshot):
    if bridge.health().get("pendingRequests") != 0:
        raise ValueError("Bridge有待处理请求，禁止增量写入")
    original = unwrap_audit(bundle["audit"])
    inventory = bridge.drawing_components(snapshot["window_id"])
    _same_state(inventory, original)
    if (inventory.get("truncated") is not False or inventory.get("components") != bundle["inventory"]["components"]):
        raise ValueError("增量写入前器件清单已变化")
    observed = unwrap_audit(bridge.design_query("audit", snapshot["window_id"]))
    _same_state(observed, original)
    if (observed["editor_version"] != original["editor_version"] or
            observed["raw_netlist"] != original["raw_netlist"] or observed["drc"] != original["drc"]):
        raise ValueError("增量写入前完整网表或详细DRC已变化")
    if bundle.get("frame_svg") is not None:
        svg = bridge.drawing_svg(snapshot["window_id"])
        _same_state(svg, original)
        rows = records(original["source"])
        if (frame_geometry(rows, inventory["components"], svg) !=
                frame_geometry(rows, inventory["components"], bundle["frame_svg"])):
            raise ValueError("增量写入前图框或标题栏几何已变化")


def capture_baseline(bridge, window_id=None):
    """Fixed getters only; every query must observe the same complete source."""
    audit = bridge.design_query("audit", window_id)
    state = unwrap_audit(audit)
    window_id = state["window_id"]
    inventory = bridge.drawing_components(window_id)
    geometry = []
    for item in inventory["components"]:
        if item["component_type"] != "sheet":
            geometry.append(bridge.design_query("source-component", window_id, item["primitive_id"]))
    bundle = {"schema_version": 1, "kind": "incremental-baseline", "created_at": now(),
              "audit": audit, "inventory": inventory, "geometry": geometry}
    if any(item["component_type"] == "sheet" for item in inventory["components"]):
        bundle["frame_svg"] = bridge.drawing_svg(window_id)
    validate_baseline(bundle)
    return bundle


def _same_state(value, audit):
    if (value.get("identity") != audit["identity"] or value.get("window_id") != audit["window_id"] or
            source_digest(value.get("source", "")) != audit["source_sha256"]):
        raise ValueError("增量基线目标、窗口或完整源不一致")


def validate_baseline(bundle):
    if (bundle.get("schema_version") != 1 or bundle.get("kind") != "incremental-baseline"):
        raise ValueError("需要完整的增量编辑基线")
    audit = unwrap_audit(bundle["audit"])
    if audit["editor_version"] != SOURCE_VERSION:
        raise ValueError("增量契约仅覆盖编辑器4.1.100")
    inventory = bundle["inventory"]
    _same_state(inventory, audit)
    items = inventory.get("components")
    if (inventory.get("truncated") is not False or not isinstance(items, list) or len(items) > 500 or
            inventory.get("total") != len(items) or any(not isinstance(c, dict) or
                c.get("component_type") not in {"sheet", "part", "netflag"} for c in items)):
        raise ValueError("增量基线器件清单不完整或含未支持对象")
    rows = records(audit["source"])
    ids = {c.get("primitive_id") for c in items}
    if len(ids) != len(items) or ids != {rid for rid, (env, _) in rows.items() if env["type"] == "COMPONENT"}:
        raise ValueError("增量基线未覆盖所有源器件")
    if any(env["type"] not in {"DOCHEAD", "CANVAS", "COMPONENT", "ATTR", "WIRE", "LINE"}
           for env, _ in rows.values()):
        raise ValueError("增量首期不支持总线或其他未核验源图形")
    expected = {c["primitive_id"] for c in items if c["component_type"] != "sheet"}
    geometries = bundle.get("geometry")
    if not isinstance(geometries, list) or len(geometries) != len(expected):
        raise ValueError("增量基线缺少全部已有器件引脚几何")
    seen, boxes = set(), []
    for query in geometries:
        if query.get("kind") != "design-query" or query.get("query") != "source-component":
            raise ValueError("几何必须来自固定Source读取")
        data = query["data"]
        _same_state(data, audit)
        if (data.get("editor_version") != SOURCE_VERSION or query.get("missing_fields") or
                query.get("target", {}).get("identity") != audit["identity"] or
                query.get("target", {}).get("window_id") != audit["window_id"]):
            raise ValueError("增量几何来源不完整")
        pid = data["primitive_id"]
        if pid not in expected or pid in seen:
            raise ValueError("增量几何器件覆盖重复或不符")
        seen.add(pid)
        outputs = data["outputs"]
        if any(outputs.get(k, {}).get("status") != "returned" for k in ("component", "pin_geometry")):
            raise ValueError("增量几何读取未返回")
        if outputs["component"]["value"].get("primitiveId") != pid:
            raise ValueError("增量几何返回其他器件")
        pins = outputs["pin_geometry"]["value"]
        if not isinstance(pins, list) or not 1 <= len(pins) <= 500:
            raise ValueError("已有器件引脚几何不完整")
        for pin in pins:
            box = pin.get("bodyBBox", {})
            b = [box.get(k) for k in ("minX", "minY", "maxX", "maxY")]
            if (not all(_number(v) for v in b) or b[0] > b[2] or b[1] > b[3] or
                    not _number(pin.get("x")) or not _number(pin.get("y"))):
                raise ValueError("已有器件边界或引脚坐标无效")
            boxes.extend([b, [pin["x"], pin["y"], pin["x"], pin["y"]]])
    if seen != expected:
        raise ValueError("增量几何覆盖缺项")
    if bundle.get("frame_svg") is not None:
        _same_state(bundle["frame_svg"], audit)
        frame_geometry(rows, items, bundle["frame_svg"])
    return audit, boxes


def load_baseline(plan, snapshot):
    ref = plan.get("incremental_baseline")
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise ValueError("增量计划须绑定baseline路径和摘要")
    path = external(Path(ref["path"]))
    if (not path.is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", str(ref["sha256"])) or
            path.stat().st_size > 40 * 1024 * 1024 or digest(path) != ref["sha256"]):
        raise ValueError("增量基线文件摘要不一致或过大")
    bundle = read_json(path)
    audit, boxes = validate_baseline(bundle)
    _same_state(snapshot, audit)
    return bundle, audit, boxes


def _overlap(a, b, padding=0):
    return (max(a[0], b[0]-padding) <= min(a[2], b[2]+padding) and
            max(a[1], b[1]-padding) <= min(a[3], b[3]+padding))


def validate_frame_box(frame, box, label):
    if frame is None:
        return
    left, bottom, right, top = frame["inner_border"]
    margin = frame["clearance"]
    if not (left + margin <= box[0] <= box[2] <= right - margin and
            bottom + margin <= box[1] <= box[3] <= top - margin):
        raise ValueError(f"{label}超出图框内边界或净距不足")
    if _overlap(box, frame["title_block"], margin):
        raise ValueError(f"{label}进入标题栏保留区")


def validate_edit_region(region, snapshot, boxes, inventory, frame_svg):
    """Keep new component space clear; callers check wire corridors separately."""
    if (not isinstance(region, list) or len(region) != 4 or not all(_number(v) for v in region) or
            not (0 <= region[0] < region[2] <= 1170 and 0 <= region[1] < region[3] <= 825)):
        raise ValueError("增量编辑区域须为A4内有效API坐标矩形")
    if any(_overlap(region, b, 20) for b in boxes):
        raise ValueError("增量区域接近已有器件本体或引脚")
    rows = records(snapshot["source"])
    frame = frame_geometry(rows, inventory, frame_svg)
    validate_frame_box(frame, region, "编辑区域")
    borders = {v.get("parentId") for e,v in rows.values() if e["type"] == "ATTR" and
               v.get("key") == "Border" and v.get("value") == "1"}
    for env, value in rows.values():
        if env["type"] == "LINE":
            b = [min(value["startX"], value["endX"]), min(-value["startY"], -value["endY"]),
                 max(value["startX"], value["endX"]), max(-value["startY"], -value["endY"])]
            if _overlap(region, b, 10):
                raise ValueError("增量区域接近已有线段")
        elif env["type"] == "WIRE" and value.get("dots") is not None:
            raise ValueError("当前增量基线须使用已核验的LINE导线格式")
        elif (env["type"] == "ATTR" and value.get("parentId") not in borders and
              _number(value.get("x")) and _number(value.get("y"))):
            # Conservative space around text anchors; no claim of font rendering.
            size = value.get("fontSize") if _number(value.get("fontSize")) else 15
            extent = max(50, size * len(str(value.get("value", ""))))
            x, y = value["x"], -value["y"]
            if _overlap(region, [x-extent, y-size*2, x+extent, y+size*2]):
                raise ValueError("增量区域接近已有属性文字锚点")
    return rows, frame


def validate_incremental(plan, snapshot):
    if plan.get("schema_version") != 2 or plan.get("execution_contract") != INCREMENTAL:
        raise ValueError("增量编辑要求v2计划和v4执行契约")
    if any(k in plan for k in ("recovery_of", "base_components", "base_flags", "base_wires")):
        raise ValueError("增量计划不能混用旧恢复基线")
    bundle, audit, boxes = load_baseline(plan, snapshot)
    region = plan.get("edit_region")
    rows, frame = validate_edit_region(region, snapshot, boxes, bundle["inventory"]["components"], bundle.get("frame_svg"))
    # Reuse the validated new-circuit geometry on an empty *offline model* only.
    # The real complete snapshot stays bound and is checked before every write.
    header = next(line for line in snapshot["source"].splitlines() if '"DOCHEAD"' in line)
    empty = {**snapshot, "source": header + "\n"}
    geometry = validate_circuit({**plan, "execution_contract": V3}, empty)
    nets = {entry["net"] for entry in plan["connectivity"]}
    baseline_nets = {p["net"] for c in components_from_raw(audit["raw_netlist"]).values() for p in c["pinInfoMap"].values()}
    baseline_nets |= {v.get("value") for e,v in rows.values() if e["type"] == "ATTR" and
                      v.get("key", "").upper() in {"NET", "GLOBAL NET NAME"} and isinstance(v.get("value"), str)}
    if nets & baseline_nets:
        raise ValueError("独立增量模块不得复用已有网络名称")
    wire_nets = [op["parameters"]["net"] for op in plan["operations"] if op["operation"] == "add_wire"]
    if len(set(wire_nets)) != len(wire_nets) or set(wire_nets) != nets:
        raise ValueError("每个新增网络必须由一个完整导线操作构成")
    for op in plan["operations"]:
        p = op["parameters"]
        if op["operation"] == "place_component":
            if "designator" in p or p["add_into_bom"] is not True or p["add_into_pcb"] is not True:
                raise ValueError("增量器件须进入完整网表，位号由编辑器生成")
            b = geometry["library_geometry"][op["id"]]["bounds"]
            points = [(b[0],b[1]),(b[2],b[3])] + [(pin["x"],pin["y"]) for pin in p["expected_pins"]]
        elif op["operation"] == "add_wire":
            points = [(a,f) for axis,f,a,b in wire_segments(p["line"]) if axis == "H"]
            points += [(b,f) for axis,f,a,b in wire_segments(p["line"]) if axis == "H"]
            points += [(f,a) for axis,f,a,b in wire_segments(p["line"]) if axis == "V"]
            points += [(f,b) for axis,f,a,b in wire_segments(p["line"]) if axis == "V"]
        else:
            points = [(p["x"],p["y"])]
        if any(not (region[0] <= x <= region[2] and region[1] <= y <= region[3]) for x,y in points):
            raise ValueError("新增对象超出声明的编辑区域")
    geometry["baseline_component_count"] = len(components_from_raw(audit["raw_netlist"]))
    geometry["baseline_drc_count"] = len(audit["drc"])
    geometry["sheet_frame"] = frame
    return geometry


def verify_incremental_reply(reply, before, op):
    if reply.get("window_id") != before["window_id"]:
        raise ValueError("增量写入返回窗口不符")
    # v4 never permits a baseline-wire merge or any changed existing record.
    pid = reply.get("primitive_id")
    if pid in records(before["source"]):
        raise ValueError("增量首期不允许合并既有导线图元")
    if op["operation"] == "place_component":
        attrs = [v for e,v in records(reply.get("source", "")).values() if e["type"] == "ATTR" and
                 v.get("parentId") == pid and v.get("key") == "Unique ID"]
        if len(attrs) != 1 or not _text(attrs[0].get("value")) or attrs[0]["value"] != reply.get("observed",{}).get("unique_id"):
            raise ValueError("新增器件Unique ID尚未稳定或源与getter不符")
    return verify_reply(reply, before, op, editor_version=SOURCE_VERSION)


def _components(raw):
    result = {}
    for component in components_from_raw(raw).values():
        uid = component["props"].get("Unique ID")
        if not _text(uid) or uid in result:
            raise ValueError("网表Unique ID缺失或重复")
        result[uid] = component
    return result


def audit_incremental(reply, snapshot, plan, baseline, result_primitives):
    before = unwrap_audit(baseline["audit"])
    after = make_audit(snapshot, reply, "0" * 64)
    if after["editor_version"] != SOURCE_VERSION:
        raise ValueError("增量审计版本不符")
    original_rows, final_rows = records(before["source"]), records(after["source"])
    changed = [rid for rid, row in original_rows.items() if final_rows.get(rid) != row]
    old, new = _components(before["raw_netlist"]), _components(after["raw_netlist"])
    changed_components = [uid for uid, c in old.items() if new.get(uid) != c]
    additions = {op["id"]: op for op in plan["operations"] if op["operation"] == "place_component"}
    if set(result_primitives) != set(additions):
        raise ValueError("增量审计缺少全部新增器件图元映射")
    expected = {(e["component"],e["pin"]):e["net"] for e in plan["connectivity"]}
    added_uids, mismatches = set(), []
    for opid, pid in result_primitives.items():
        attrs = {v.get("key"):v.get("value") for e,v in final_rows.values() if e["type"] == "ATTR" and v.get("parentId") == pid}
        uid = attrs.get("Unique ID")
        if uid in old or uid in added_uids or not _text(uid):
            raise ValueError("新增器件Unique ID不唯一或与基线冲突")
        added_uids.add(uid)
        c = new.get(uid)
        if c is None or any(c["props"].get(k) != attrs.get(k) for k in ("Designator","Device","Unique ID")):
            raise ValueError("新增Source器件没有进入对应的完整网表")
        observed = {pin["number"]:pin["net"] for pin in c["pinInfoMap"].values()}
        wanted = {n:net for (key,n),net in expected.items() if key == opid}
        if len(observed) != len(c["pinInfoMap"]) or observed != wanted:
            mismatches.append({"operation":opid,"expected":wanted,"observed":observed})
    extras = sorted(set(new) - set(old) - added_uids)
    delta = _drc_delta(before["drc"],after["drc"])
    drc_unchanged = not any(delta[k] for k in ("added","removed","changed")) and not any(delta["unmatched"].values())
    matches = not changed and not changed_components and not extras and not mismatches
    return {"contract":INCREMENTAL,"baseline_source_preserved":not changed,"changed_original_records":changed,
            "baseline_components_preserved":not changed_components,"changed_original_components":changed_components,
            "new_component_count":len(added_uids),"unexpected_components":extras,
            "connectivity_matches":matches,"connectivity_mismatches":mismatches,
            "drc_passed":drc_unchanged,"drc_unchanged":drc_unchanged,"drc_delta":delta,
            "baseline_drc_count":len(before["drc"]),"final_drc_count":len(after["drc"]),
            "verification_scope":"isolated-additions-and-preserved-baseline",
            "drc_global_freshness_established":False,"engineering_verified":False}
