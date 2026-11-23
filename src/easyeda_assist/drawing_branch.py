"""Bounded physical branches onto explicitly identified existing wires.

The v4 isolated-addition contract stays unchanged. v5 allows one contact point
per new wire, preserves every old segment, and verifies complete wire coverage
and old component objects. No generic move, delete, reconnect or Source write.
"""
from __future__ import annotations

from .drawing_api import validate_snapshot
from .drawing_circuit import (validate_circuit, baseline_wire_nets, point_on,
                             intersects, _number, _text, verify_reply)
from .drawing_incremental import (load_baseline, validate_edit_region, validate_frame_box, _overlap,
                                 verify_incremental_reply, audit_incremental)
from .drawing_source import records, wire_segments
from .operations import BRANCH, V3, SOURCE_VERSION


def source_line(rows, pid):
    if pid not in rows or rows[pid][0]["type"] != "WIRE" or rows[pid][1].get("dots") is not None:
        raise ValueError("支路基线必须使用已核验的WIRE/LINE格式")
    line = [[v["startX"], -v["startY"], v["endX"], -v["endY"]]
            for e, v in rows.values() if e["type"] == "LINE" and v.get("lineGroup") == pid]
    wire_segments(line)
    return line


def _contact(a, b):
    """An intersection must be one exact point, never a collinear overlap."""
    if not intersects(a, b):
        return None
    if a[0] != b[0]:
        h, v = (a, b) if a[0] == "H" else (b, a)
        return v[1], h[1]
    start, end = max(a[2], b[2]), min(a[3], b[3])
    if start != end:
        raise ValueError("新增支路不得覆盖已有导线")
    return (start, a[1]) if a[0] == "H" else (a[1], start)


def _box(segment):
    axis, fixed, lo, hi = segment
    return [lo, fixed, hi, fixed] if axis == "H" else [fixed, lo, fixed, hi]


def _crosses_interior(segment, box):
    axis, fixed, lo, hi = segment
    return (box[1] < fixed < box[3] and max(lo, box[0]) < min(hi, box[2])) if axis == "H" else (
        box[0] < fixed < box[2] and max(lo, box[1]) < min(hi, box[3]))


def anchors_by_operation(plan):
    anchors = plan.get("branch_anchors")
    if not isinstance(anchors, list) or not 1 <= len(anchors) <= 8:
        raise ValueError("支路必须声明1至8个接入锚点")
    result, targets, nets = {}, set(), set()
    for a in anchors:
        if (not isinstance(a, dict) or set(a) != {"operation", "primitive_id", "net", "x", "y"} or
                any(not _text(a[k]) for k in ("operation", "primitive_id", "net")) or
                any(not _number(a[k]) for k in ("x", "y"))):
            raise ValueError("支路锚点字段无效")
        if a["operation"] in result or a["primitive_id"] in targets or a["net"] in nets:
            raise ValueError("每个支路、目标导线及网络只能声明一次")
        result[a["operation"]] = a
        targets.add(a["primitive_id"])
        nets.add(a["net"])
    return result


def validate_branch(plan, snapshot):
    if plan.get("schema_version") != 2 or plan.get("execution_contract") != BRANCH:
        raise ValueError("支路编辑要求v2计划和v5执行契约")
    if any(k in plan for k in ("recovery_of", "base_components", "base_flags", "base_wires")):
        raise ValueError("支路计划不能混用旧恢复基线")
    bundle, audit, boxes = load_baseline(plan, snapshot)
    region = plan.get("edit_region")
    rows, frame = validate_edit_region(region, snapshot, boxes, bundle["inventory"]["components"], bundle.get("frame_svg"))
    anchors = anchors_by_operation(plan)
    wires = {op["id"]: op for op in plan["operations"] if op["operation"] == "add_wire"}
    components = [op for op in plan["operations"] if op["operation"] == "place_component"]
    if (set(wires) != set(anchors) or not 1 <= len(components) <= 4 or
            len(components) + len(wires) != len(plan["operations"])):
        raise ValueError("支路仅支持1至4个新增器件及逐条绑定锚点的导线")
    # Reuse v3 only on an empty offline model for new-pin topology/library checks.
    header = next(line for line in snapshot["source"].splitlines() if '"DOCHEAD"' in line)
    geometry = validate_circuit({**plan, "execution_contract": V3}, {**snapshot, "source": header + "\n"})
    new_pins = {(p["x"], p["y"]): (op["id"], p["number"])
                for op in components for p in op["parameters"]["expected_pins"]}
    expected = {(e["component"], e["pin"]): e["net"] for e in plan["connectivity"]}
    if set(expected.values()) != {a["net"] for a in anchors.values()}:
        raise ValueError("每个新增引脚必须进入已声明的支路网络")
    for op in components:
        p = op["parameters"]
        if "designator" in p or p["add_into_bom"] is not True or p["add_into_pcb"] is not True:
            raise ValueError("支路器件须进入完整网表，位号由编辑器生成")
        b = geometry["library_geometry"][op["id"]]["bounds"]
        points = [(b[0], b[1]), (b[2], b[3])] + [(pin["x"], pin["y"]) for pin in p["expected_pins"]]
        if any(not (region[0] <= x <= region[2] and region[1] <= y <= region[3]) for x, y in points):
            raise ValueError("新增器件超出声明的空闲区域")
    old_lines = [(v.get("lineGroup"), (v["startX"], -v["startY"], v["endX"], -v["endY"]))
                 for e, v in rows.values() if e["type"] == "LINE"]
    old_parts = {c["primitive_id"] for c in bundle["inventory"]["components"] if c["component_type"] != "sheet"}
    for oid, op in wires.items():
        p, a = op["parameters"], anchors[oid]
        line = p["line"]
        if (not isinstance(line, list) or any(isinstance(v, list) for v in line) or
                p["net"] != a["net"]):
            raise ValueError("支路必须为单条正交折线并保持锚点网名")
        ends = [(line[0], line[1]), (line[-2], line[-1])]
        anchor = (a["x"], a["y"])
        if anchor not in ends or ends[0] == ends[1]:
            raise ValueError("支路端点必须精确落在声明的旧线锚点")
        other = ends[1] if ends[0] == anchor else ends[0]
        if other not in new_pins or expected[new_pins[other]] != a["net"]:
            raise ValueError("支路另一端必须落在对应的新器件引脚")
        baseline = source_line(rows, a["primitive_id"])
        styles = [{k:x for k,x in v.items() if k not in {"startX","startY","endX","endY"}}
                  for e,v in rows.values() if e["type"]=="LINE" and v.get("lineGroup")==a["primitive_id"]]
        if any(style!=styles[0] for style in styles):
            raise ValueError("支路目标导线样式必须一致")
        if (baseline_wire_nets(rows, a["primitive_id"], baseline) != {a["net"]} or
                not any(point_on(anchor, s) for s in wire_segments(baseline))):
            raise ValueError("锚点不在具有唯一指定网名的旧导线上")
        for group, old_line in old_lines:
            if group != a["primitive_id"] and any(intersects(s, wire_segments(list(old_line))[0]) for s in wire_segments(baseline)):
                raise ValueError("目标旧导线接触其他导线组，可能触发删除合并，禁止支路写入")
        segments = wire_segments(line)
        if any(not (0 <= x <= 1170 and 0 <= y <= 825) for x, y in zip(line[::2], line[1::2])):
            raise ValueError("支路路径超出A4图框")
        for segment in segments:
            validate_frame_box(frame, _box(segment), "支路路径")
        contacts = set()
        for group, old_line in old_lines:
            old_segment = wire_segments(list(old_line))[0]
            for segment in segments:
                point = _contact(segment, old_segment)
                if point is not None:
                    if group != a["primitive_id"] or point != anchor:
                        raise ValueError("支路触及未声明的旧导线或接入点")
                    contacts.add(point)
        if contacts != {anchor}:
            raise ValueError("支路缺少唯一物理接触")
        if any(_overlap(_box(s), b, 5) for s in segments for b in boxes):
            raise ValueError("支路路径接近已有器件本体或引脚")
        for g in geometry["library_geometry"].values():
            b = g["bounds"]
            for segment in segments:
                if _crosses_interior(segment,b):
                    raise ValueError("支路路径穿过新增器件本体")
        for e, v in rows.values():
            if (e["type"] == "ATTR" and v.get("parentId") in old_parts and
                    _number(v.get("x")) and _number(v.get("y")) and
                    (v.get("key") != "Symbol" or v.get("keyVisible") is True or v.get("valueVisible") is True) and
                    (v.get("keyVisible") is True or (v.get("value") is not None and v.get("valueVisible") is not False))):
                size = v.get("fontSize") if _number(v.get("fontSize")) else 15
                extent = max(50, size * len(str(v.get("value", ""))))
                b = [v["x"]-extent, -v["y"]-size*2, v["x"]+extent, -v["y"]+size*2]
                if any(_crosses_interior(s, b) for s in segments):
                    raise ValueError("支路路径接近已有器件属性文字锚点")
    from .schematic import components_from_raw
    geometry.update(baseline_component_count=len(components_from_raw(audit["raw_netlist"])),
                    baseline_drc_count=len(audit["drc"]), branch_anchors=list(anchors.values()),
                    sheet_frame=frame)
    return geometry


def _target_attr_change(old, new, targets):
    e, v = old
    en, vn = new
    return (e == en and e["type"] == "ATTR" and v.get("parentId") in targets and
            v.get("key") in {"NET", "Relevance"} and type(vn.get("zIndex")) is int and
            {k: val for k, val in v.items() if k != "zIndex"} ==
            {k: val for k, val in vn.items() if k != "zIndex"})


def getter_segments(value):
    """4.1.100 getters serialize edge pairs, including disjoint traversal edges.

    create() accepts polylines; getState_Line() was observed returning a flat
    concatenation of four-coordinate LINE edges. Never invent joining edges.
    """
    if not isinstance(value, list) or not value:
        raise ValueError("支路getter缺少完整边数组")
    groups = value if isinstance(value[0], list) else [value[i:i+4] for i in range(0, len(value), 4)]
    if any(not isinstance(g, list) or len(g) != 4 for g in groups):
        raise ValueError("支路getter必须由完整的四坐标边构成")
    return wire_segments(groups)


def merge_effects(old, new, op, anchor):
    """Exact edge multiset despite target LINE coordinate reassignment."""
    pid, net = anchor["primitive_id"], anchor["net"]
    if old.get(pid) != new.get(pid) or pid not in old:
        raise ValueError("支路不能替换或改变旧WIRE记录")
    from collections import Counter
    changes, split, added = set(), [], set()
    coordinates = {"startX", "startY", "endX", "endY"}
    expected_edges, original_edges, styles = [], [], []
    for rid, (e, v) in old.items():
        if e["type"] != "LINE" or v.get("lineGroup") != pid:
            continue
        segment = wire_segments([v["startX"], -v["startY"], v["endX"], -v["endY"]])[0]
        original_edges.append(segment)
        styles.append({k:x for k,x in v.items() if k not in coordinates})
        axis, fixed, lo, hi = segment
        at = anchor["x"] if axis == "H" else anchor["y"]
        if point_on((anchor["x"],anchor["y"]),segment) and lo < at < hi:
            expected_edges.extend([(axis,fixed,lo,at),(axis,fixed,at,hi)])
            split.append(rid)
        else:
            expected_edges.append(segment)
    if len(split)>1 or not styles or any(style!=styles[0] for style in styles):
        raise ValueError("目标导线须为一致样式，锚点至多在一个旧段内部")
    planned=op["parameters"]["line"]
    new_edges=[wire_segments([x1,y1,x2,y2])[0] for x1,y1,x2,y2 in
               zip(planned[0::2],planned[1::2],planned[2::2],planned[3::2])]
    expected_edges.extend(new_edges)
    for rid, row in old.items():
        if new.get(rid)==row:continue
        if rid not in new:raise ValueError("支路不能删除旧记录")
        e,v=row;en,vn=new[rid]
        if _target_attr_change(row,new[rid],{pid}):
            changes.add(rid)
        elif e["type"]=="LINE" and v.get("lineGroup")==pid:
            if en!=e or {k:x for k,x in vn.items() if k not in coordinates}!=styles[0]:
                raise ValueError("目标LINE身份或样式改变")
            changes.add(rid)
        # Caller rejects all other changed records.
    attributes = set()
    for rid in set(new)-set(old):
        e,v = new[rid]
        if e["type"] == "LINE" and v.get("lineGroup") == pid:
            if {k:x for k,x in v.items() if k not in coordinates} != styles[0]:
                raise ValueError("新增支路线段样式与目标不符")
            added.add(rid)
        elif e["type"] == "ATTR" and v.get("parentId") == pid:
            key=v.get("key")
            if key not in {"NET","Relevance"} or key in attributes or v.get("value") != (net if key=="NET" else "[]"):
                raise ValueError("支路新增了未授权或重复的网络属性")
            attributes.add(key);added.add(rid)
    actual_edges = [wire_segments(edge)[0] for edge in source_line(new,pid)]
    if Counter(actual_edges) not in (Counter(expected_edges),Counter(original_edges+new_edges)):
        raise ValueError("目标边必须精确等于旧边（仅锚点二分）与计划支路边")
    if wire_segments(source_line(new,pid)) != wire_segments(source_line(old,pid)+[planned]):
        raise ValueError("支路完整覆盖丢失旧线或增加方案外几何")
    if baseline_wire_nets(new,pid,source_line(new,pid)) != {net}:
        raise ValueError("支路改变了旧网络名称")
    return added, changes, split


def verify_branch_reply(reply, before, op, plan):
    if op["operation"] == "place_component":
        return verify_incremental_reply(reply, before, op)
    a = anchors_by_operation(plan)[op["id"]]
    if (reply.get("window_id") != before["window_id"] or reply.get("identity") != before["identity"] or
            reply.get("attempted") is not True or reply.get("status") != "verified"):
        raise ValueError("支路回读身份或状态不符")
    pid, old = reply.get("primitive_id"), records(before["source"])
    after = {**before, "source": reply.get("source"), "identity": reply.get("identity")}
    validate_snapshot(after)
    if pid in old:
        if pid != a["primitive_id"]:
            raise ValueError("支路合并到了未声明的旧导线")
        new = records(after["source"])
        added, changes, _ = merge_effects(old,new,op,a)
        if set(new)-set(old) != added or any(new.get(rid)!=row and rid not in changes for rid,row in old.items()):
            raise ValueError("支路改变了许可分段或网络属性以外的记录")
        wanted = source_line(old, pid) + [op["parameters"]["line"]]
    else:
        after = verify_reply(reply, before, op, editor_version=SOURCE_VERSION)
        wanted = [op["parameters"]["line"]]
    observed = reply.get("observed", {})
    new = records(after["source"])
    if (observed.get("primitive_id") != pid or observed.get("net") != a["net"] or
            getter_segments(observed.get("line")) != wire_segments(wanted) or
            wire_segments(source_line(new, pid)) != wire_segments(wanted) or
            baseline_wire_nets(new, pid, source_line(new, pid)) != {a["net"]}):
        raise ValueError("支路完整导线getter、源覆盖或网名不符")
    return after


def audit_branch(reply, snapshot, plan, baseline, result_primitives):
    """Recheck cumulative source effects independently of step receipts."""
    additions = {op["id"]: op for op in plan["operations"] if op["operation"] == "place_component"}
    anchors = anchors_by_operation(plan)
    if set(result_primitives) != set(additions) | set(anchors):
        raise ValueError("支路审计缺少全部器件及导线图元映射")
    summary = audit_incremental(reply, snapshot, plan, baseline,
                                {oid: result_primitives[oid] for oid in additions})
    from .design_audit import unwrap_audit
    old = records(unwrap_audit(baseline["audit"])["source"])
    new = records(reply["source"])
    changed = [rid for rid, row in old.items() if new.get(rid) != row]
    allowed_changes, allowed_additions, splits = set(), set(), []
    unauthorized = []
    source_ok = True
    coverage = []
    new_parts = {result_primitives[oid] for oid in additions}
    wire_groups = {}
    for oid, a in anchors.items():
        pid = result_primitives[oid]
        if pid in wire_groups or (pid in old and pid != a["primitive_id"]):
            raise ValueError("支路审计导线身份冲突")
        wire_groups[pid] = a
        planned = next(op["parameters"]["line"] for op in plan["operations"] if op["id"] == oid)
        if pid == a["primitive_id"]:
            op = next(op for op in plan["operations"] if op["id"] == oid)
            added, changes, split = merge_effects(old,new,op,a)
            allowed_additions |= added;allowed_changes |= changes;splits.extend(split)
        wanted = source_line(old, a["primitive_id"]) + [planned] if pid == a["primitive_id"] else [planned]
        match = (wire_segments(source_line(new, pid)) == wire_segments(wanted) and
                 baseline_wire_nets(new, pid, source_line(new, pid)) == {a["net"]})
        coverage.append({"operation": oid, "primitive_id": pid, "target": a["primitive_id"], "matches": match})
        source_ok &= match
    unauthorized.extend(rid for rid in changed if rid not in allowed_changes)
    source_ok &= not unauthorized
    attr_keys = set()
    for rid in set(new) - set(old):
        if rid in allowed_additions:
            continue
        e, v = new[rid]
        if rid in new_parts and e["type"] == "COMPONENT":
            continue
        if e["type"] == "ATTR" and v.get("parentId") in new_parts:
            continue
        if rid in wire_groups and e["type"] == "WIRE":
            continue
        if e["type"] == "LINE" and v.get("lineGroup") in wire_groups:
            continue
        if (e["type"] == "ATTR" and v.get("parentId") in wire_groups and
                v.get("key") in {"NET", "Relevance"}):
            key = (v["parentId"], v["key"])
            expected_value = wire_groups[v["parentId"]]["net"] if v["key"] == "NET" else "[]"
            if key not in attr_keys and v.get("value") == expected_value:
                attr_keys.add(key)
                continue
        unauthorized.append(rid)
        source_ok = False
    summary.update(contract=BRANCH, baseline_source_within_contract=bool(source_ok),
                   unauthorized_source_records=sorted(set(unauthorized)), wire_coverage=coverage,
                   split_original_lines=splits,
                   verification_scope="physical-branches-and-preserved-existing-components")
    summary["connectivity_matches"] = bool(source_ok and summary["baseline_components_preserved"] and
        not summary["unexpected_components"] and not summary["connectivity_mismatches"])
    return summary
