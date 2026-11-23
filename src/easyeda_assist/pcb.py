"""PCB intent, immutable baselines, constraints, scope, and source-delta checks.

Engineering reasoning belongs to the agent. The native EDA engine supplies
candidate placement/routing; this module checks declared bounds, never SI/PI.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from .pcb_api import FIELDS, LAYER_NAMES
from .schematic import components_from_raw
from .storage import digest, external, initialize, new_id, now, read_json, write_new

CONTRACT = "pcb-drawing-v1"
INTENTS = {
    "full": "完成整板 PCB；按布局、关键网络、普通网络、铺铜与复核逐轮推进",
    "partial": "只修改明确的器件、网络和区域，保留其余设计",
    "advice": "对特定网络提供路径、约束、取舍和验证建议，不写 CAD",
    "layout-critical": "完成指定器件布局及关键网络，普通网络交回用户",
}
OPERATIONS = ("move_component", "add_line", "add_via", "add_outline", "add_pour", "rebuild_pour", "route_nets", "auto_place")
LIMITATIONS = [
    "4.1.100已有局部PCB工具链实测：移动/旋转、顶底线、通孔、板框、铜区重建、单网络布线及导出；参数边界见docs/pcb-live-verification.md。写入仍须方案认可及显式experimental选择。",
    "自动布局/布线产生候选结果；物理回流、温升、阻抗、时序、EMC 与实物验收仍需专项证据。",
    "不自动保存、下单、删除或回滚；执行期间避免同时编辑。未知状态保留日志和锁，不自动重试。",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def number(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1000000
            and (not positive or value > 0))


def string_list(value, *, empty=False):
    return (isinstance(value, list) and (empty or bool(value)) and all(text(v) for v in value)
            and len(set(value)) == len(value))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON 重复字段")
        result[key] = value
    return result


def comparable_source(source):
    head, separator, tail = source.partition('\n')
    env, delimiter, value = head.partition('||')
    require(delimiter and value.rstrip().endswith('|'), "PCB源文件头格式无效")
    envelope = json.loads(env, object_pairs_hook=_unique)
    payload = json.loads(value.rstrip()[:-1], object_pairs_hook=_unique)
    require(envelope.get('type') == 'DOCHEAD' and payload.get('docType') == 'PCB', "PCB源文件头类型无效")
    for key in ('client', 'updateTime', 'version'):
        payload.pop(key, None)
    return json.dumps(envelope, sort_keys=True) + '||' + json.dumps(payload, sort_keys=True) + '|' + separator + tail


def source_records(source):
    """Read v2 object records including repeated PAD_NET and non-string layer IDs.

    Keep record order and all header fields. Unknown records are preserved, not
    discarded. Unsupported legacy array sources remain readable as raw capture,
    but cannot be used to approve writes.
    """
    require(isinstance(source, str) and 0 < len(source.encode("utf-8")) <= 20*1024*1024,
            "需要20MiB以内的PCB完整源")
    result = {}
    lines = comparable_source(source).splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        outer, separator, inner = line.partition("||")
        require(separator and (inner.rstrip().endswith("|") or index == len(lines)-1), "PCB源格式尚未适配；需要v2对象记录")
        env = json.loads(outer, object_pairs_hook=_unique)
        inner = inner.rstrip()
        value = json.loads(inner[:-1] if inner.endswith('|') else inner, object_pairs_hook=_unique)
        require(isinstance(env, dict) and isinstance(value, dict) and text(env.get("type")), "PCB源记录无效")
        kind = env["type"]
        key = (kind, json.dumps(env.get("id"), sort_keys=True),
               json.dumps([value.get("padNum"), value.get("padId")]) if kind == "PAD_NET" else "")
        require(key not in result, "PCB源记录身份重复")
        result[key] = (env, value)
    heads = [v for k, v in result.items() if k[0] == "DOCHEAD"]
    require(len(heads) == 1 and heads[0][1].get("docType") == "PCB", "完整源不是PCB")
    return result


def validate_snapshot(snapshot):
    require(isinstance(snapshot, dict) and snapshot.get("schema_version") == 1
            and snapshot.get("kind") == "pcb-snapshot", "需要 pcb-capture 快照")
    require(text(snapshot.get("window_id")), "PCB快照缺少窗口身份")
    identity = snapshot.get("identity")
    require(isinstance(identity, dict) and all(text(identity.get(k)) for k in
        ("project_uuid", "document_uuid", "tab_id", "editor_version")), "PCB身份或版本缺失")
    require(isinstance(snapshot.get("source"), str) and snapshot["source"], "PCB完整源缺失")
    require(snapshot.get("source_sha256") == hashlib.sha256(snapshot["source"].encode()).hexdigest(), "PCB源哈希不一致")
    require(isinstance(snapshot.get("collections"), dict) and isinstance(snapshot.get("outputs"), dict), "PCB读取结果不完整")
    require(snapshot.get("api_unit") == "mil" and snapshot.get("coordinate_system") == "data-origin", "PCB坐标单位/原点不兼容")
    for kind, rows in snapshot["collections"].items():
        require(kind in FIELDS and isinstance(rows, list), "PCB图元集合不兼容")
        ids = set()
        for row in rows:
            require(isinstance(row, dict) and text(row.get("id")) and row["id"] not in ids
                    and row.get("kind") == kind and isinstance(row.get("properties"), dict), "PCB图元身份无效/重复")
            require(row["properties"].get("PrimitiveId") == row["id"], "PCB图元属性与身份不一致")
            ids.add(row["id"])
    return snapshot


def make_snapshot(raw, window_id, port=None):
    snapshot = {**raw, "schema_version": 1, "kind": "pcb-snapshot", "captured_at": now(),
                "window_id": window_id, "bridge_port": port,
                "source_sha256": hashlib.sha256(raw["source"].encode()).hexdigest(),
                "comparable_sha256": hashlib.sha256(comparable_source(raw["source"]).encode()).hexdigest(),
                "limitations": LIMITATIONS[:]}
    validate_snapshot(snapshot)
    return snapshot


def output(snapshot, name):
    result = snapshot["outputs"].get(name, {})
    require(result.get("status") == "returned" and result.get("value") is not None, "PCB缺少读取证据：" + name)
    return result["value"]


def components(snapshot):
    return {row["id"]: row for row in snapshot["collections"].get("Component", [])}


def nets(snapshot):
    values = output(snapshot, "nets")
    require(isinstance(values, list) and all(isinstance(v, dict) and text(v.get("net")) for v in values), "PCB网络表不兼容")
    names = [v["net"] for v in values]
    require(len(set(names)) == len(names), "PCB网络身份重复")
    return set(names)


def _schematic(path, snapshot):
    record = read_json(path)
    require(record.get("kind") == "schematic-connectivity" and record.get("schema_version") == 1, "需要已完成原理图的 schematic --save 快照")
    require(record.get("project", {}).get("uuid") == snapshot["identity"]["project_uuid"], "原理图与PCB工程不同")
    board = output(snapshot, "board")
    schematic_document = record.get("document", {})
    if isinstance(board, dict) and isinstance(board.get("schematic"), dict):
        linked = board["schematic"].get("uuid")
        parent = schematic_document.get("parentSchematicUuid")
        if linked and parent:
            require(linked == parent, "原理图与PCB属于不同板卡的原理图")
    raw = record.get("raw_netlist")
    require(isinstance(raw, str) and hashlib.sha256(raw.encode()).hexdigest() == record.get("raw_sha256"), "原理图网表哈希不一致")
    return record, components_from_raw(raw)


def check_pin_mapping(snapshot, schematic_components, exclusions):
    require(isinstance(exclusions, dict) and all(k in schematic_components and text(v)
            for k, v in exclusions.items()), "原理图排除项需要精确元件键和理由")
    expected, observed = {}, {}
    for cid, comp in schematic_components.items():
        if cid in exclusions:
            continue
        ref = comp["props"].get("Designator")
        require(text(ref) and ref not in expected, "原理图位号缺失或重复；须显式排除非PCB对象")
        pins = {}
        for pin in comp["pinInfoMap"].values():
            num = pin["number"]
            require(num not in pins or pins[num] == pin["net"], "原理图重复引脚号映射冲突")
            pins[num] = pin["net"]
        expected[ref] = pins
    for comp in components(snapshot).values():
        ref = comp["properties"].get("Designator")
        require(text(ref) and ref not in observed and isinstance(comp.get("pads"), list), "PCB位号或焊盘不完整")
        pins = {}
        for pad in comp["pads"]:
            p = pad["properties"]
            num, net = p.get("PadNumber"), p.get("Net")
            require(text(num) and isinstance(net, str), "PCB焊盘号/网络缺失")
            require(num not in pins or pins[num] == net, "PCB同号焊盘映射冲突")
            pins[num] = net
        observed[ref] = pins
    require(expected == observed, "原理图与PCB位号/引脚网络不一致；先同步PCB或核对明确的非PCB排除项")
    return {"component_count": len(expected), "mapping_sha256": sha(expected)}


def inspect(snapshot, net=None, component_id=None):
    validate_snapshot(snapshot)
    require(not (net is not None and component_id is not None), "只选网络或器件之一")
    names, comps = nets(snapshot), components(snapshot)
    require(net is None or net in names, "PCB中没有该精确网络名")
    require(component_id is None or component_id in comps, "PCB中没有该器件ID")
    selected = {component_id: comps[component_id]} if component_id else comps
    endpoints = []
    for cid, comp in selected.items():
        for pad in comp.get("pads", []):
            p = pad["properties"]
            if net is None or p.get("Net") == net:
                endpoints.append({"component_id": cid, "designator": comp["properties"].get("Designator"),
                                  "pad_id": pad["id"], "properties": p})
    tracks = [r for k in ("Line", "Arc", "Via", "Polyline", "Pour")
              for r in snapshot["collections"].get(k, []) if net is not None and r["properties"].get("Net") == net]
    return {"identity": snapshot["identity"], "source_sha256": snapshot["source_sha256"],
            "counts": {k: len(v) for k, v in snapshot["collections"].items()},
            "complete": snapshot.get("complete"), "read_errors": snapshot.get("read_errors"),
            "nets": sorted(names), "components": list(selected.values()), "endpoints": endpoints,
            "tracks": tracks, "outputs": snapshot["outputs"], "limitations": LIMITATIONS[:]}


def workflow(intent):
    require(intent in INTENTS, "未知PCB用途")
    return {"intent": intent, "goal": INTENTS[intent], "prerequisite": "原理图完成，并在目标PCB中同步封装与引脚网络",
        "steps": ["原理图网表与目标PCB完整采集，核对引脚映射和机械/制造边界",
                  "按器件、网络、区域定义范围，登记高功率/高速约束及证据",
                  "生成具体布局/关键路径候选，离线预检后展示可执行方案",
                  "建议任务直接给结论；绘制任务在方案认可范围内按批次执行",
                  "每批回读完整源、检查详细PCB DRC及范围外对象；新布局重新采集再规划关键线",
                  "局部任务列出交回用户的剩余网络；整板任务继续普通网络和铜区，再复核制造包",
                  "样板按负载/温升/目标接口速率及同板干扰计划验证"],
        "limitations": LIMITATIONS[:], "independent_execution": "agent负责推理和方案，固定工具负责CAD操作与复核"}


def new_plan(snapshot_path, schematic_path, intent, request, component_ids=None, net_names=None):
    require(intent in INTENTS and text(request), "PCB用途和目标必填")
    snapshot_path, schematic_path = external(snapshot_path), external(schematic_path)
    snapshot = validate_snapshot(read_json(snapshot_path))
    _schematic(schematic_path, snapshot)
    comps, names = components(snapshot), nets(snapshot)
    selected_components = list(component_ids or [])
    selected_nets = list(net_names or [])
    if intent == "full":
        selected_components, selected_nets = sorted(comps), sorted(names)
    require(string_list(selected_components, empty=True) and set(selected_components) <= set(comps), "指定器件不存在或重复")
    require(string_list(selected_nets, empty=True) and set(selected_nets) <= names, "指定网络不存在或重复")
    require(intent == "full" or bool(selected_components or selected_nets), "局部任务必须显式指定器件或网络")
    plan = {"schema_version": 1, "kind": "pcb-plan", "contract": CONTRACT, "created_at": now(),
        "intent": intent, "request": request,
        "baseline": {"path": str(snapshot_path), "sha256": digest(snapshot_path)},
        "schematic": {"path": str(schematic_path), "sha256": digest(schematic_path)},
        "scope": {"component_ids": selected_components, "nets": selected_nets},
        "excluded_schematic_components": {}, "constraints": {}, "operations": [],
        "recommendations": [], "unresolved": ["补充工况、坐标核验、层叠/制造规则证据与具体方案"],
        "validation_plan": ["PCB DRC与引脚映射", "关键路径/回流复核", "适用的温升/接口及同板干扰测试"]}
    return write_new(initialize() / "pcb-plans" / (new_id("pcb-plan") + ".json"), plan)


def validate_operation(operation):
    require(isinstance(operation, dict) and set(operation) == {"id", "operation", "parameters", "rationale"}
            and text(operation["id"]) and text(operation["rationale"]), "PCB操作需要id/operation/parameters/rationale，不接受脚本")
    kind, p = operation["operation"], operation["parameters"]
    require(kind in OPERATIONS and isinstance(p, dict), "未知PCB操作")
    keys = {"move_component": {"primitive_id", "x", "y", "rotation"},
        "add_line": {"net", "layer", "start", "end", "width"},
        "add_via": {"net", "x", "y", "hole", "diameter"},
        "add_outline": {"polygon", "width"},
        "add_pour": {"net", "layer", "polygon", "name", "priority", "width"},
        "rebuild_pour": {"primitive_id"}, "route_nets": {"nets", "layers"}, "auto_place": set()}
    require(set(p) == keys[kind], "PCB操作参数不符合固定契约：" + kind)
    if kind in {"move_component", "rebuild_pour"}:
        require(text(p["primitive_id"]), "器件/铜区ID必填")
    if kind in {"move_component", "add_via"}:
        require(number(p["x"]) and number(p["y"]), "坐标必须为有限mil数值")
    if kind == "move_component":
        require(number(p["rotation"]) and 0 <= p["rotation"] < 360, "旋转角度范围0–360")
    if kind in {"add_line", "add_pour"}:
        require(isinstance(p["net"], str) and p["layer"] in LAYER_NAMES and p["layer"] != "MULTI", "网络或图层无效")
        require(number(p["width"], positive=True), "线宽必须为正mil数值")
    if kind == "add_line":
        require(all(isinstance(p[k], list) and len(p[k]) == 2 and all(number(v) for v in p[k])
                    for k in ("start", "end")) and p["start"] != p["end"], "线段需要两个不同坐标点")
    if kind == "add_via":
        require(text(p["net"]) and number(p["hole"], positive=True)
                and number(p["diameter"], positive=True) and p["diameter"] > p["hole"], "过孔网络/孔径/外径无效")
    if kind in {"add_pour", "add_outline"}:
        require(number(p["width"], positive=True), "多边形线宽必须为正数")
        if kind == "add_pour":
            require(p["layer"] != "BOARD_OUTLINE" and text(p["net"]) and text(p["name"])
                    and type(p["priority"]) is int and 0 <= p["priority"] <= 1000, "铜区网络/层/名称/优先级无效")
        polygon = p["polygon"]
        require(isinstance(polygon, list) and 9 <= len(polygon) <= 403 and polygon[2] == "L"
                and len(polygon) % 2 == 1 and all(number(v) for i, v in enumerate(polygon) if i != 2), "铜区只接受至少四点的闭合直线多边形")
        points = [polygon[:2]] + [polygon[i:i+2] for i in range(3, len(polygon), 2)]
        require(points[0] == points[-1] and len({tuple(p) for p in points[:-1]}) >= 3, "铜区多边形未闭合或退化")
    if kind == "route_nets":
        require(string_list(p["nets"]) and string_list(p["layers"])
                and all(v in LAYER_NAMES and v not in {"MULTI", "BOARD_OUTLINE"} for v in p["layers"]), "自动布线必须指定非空网络和铜层，空列表会扩大到全板")


def _load_bound(binding):
    require(isinstance(binding, dict) and set(binding) == {"path", "sha256"}, "PCB基线绑定无效")
    require(Path(binding["path"]).is_absolute(), "PCB基线必须为外部绝对路径")
    path = external(Path(binding["path"]))
    require(digest(path) == binding["sha256"], "PCB基线或原理图文件已变化")
    return path, read_json(path)


def preview(plan):
    require(isinstance(plan, dict) and plan.get("schema_version") == 1 and plan.get("kind") == "pcb-plan"
            and plan.get("contract") == CONTRACT and plan.get("intent") in INTENTS, "PCB方案格式不兼容")
    allowed = {"schema_version", "kind", "contract", "created_at", "intent", "request", "baseline", "schematic",
               "scope", "excluded_schematic_components", "constraints", "operations", "recommendations", "unresolved", "validation_plan"}
    require(set(plan) == allowed and text(plan["request"]), "PCB方案字段不完整或包含未知字段")
    _, snapshot = _load_bound(plan["baseline"])
    validate_snapshot(snapshot)
    schematic_path, _ = _load_bound(plan["schematic"])
    _, sch_components = _schematic(schematic_path, snapshot)
    findings = []
    def check(condition, message):
        if not condition:
            findings.append(message)
    for name in ("recommendations", "unresolved", "validation_plan"):
        require(isinstance(plan[name], list) and all(text(x) for x in plan[name]), "PCB方案说明字段必须为文字列表")
    check(not plan["unresolved"], "方案仍有未决项")
    check(bool(plan["validation_plan"]), "缺少验证计划")
    try:
        mapping = check_pin_mapping(snapshot, sch_components, plan["excluded_schematic_components"])
    except ValueError as e:
        findings.append(str(e)); mapping = None
    scope = plan["scope"]
    require(isinstance(scope, dict) and set(scope) == {"component_ids", "nets"}
            and string_list(scope["component_ids"], empty=True) and string_list(scope["nets"], empty=True), "PCB范围需显式器件及网络列表")
    comps, names = components(snapshot), nets(snapshot)
    require(set(scope["component_ids"]) <= set(comps) and set(scope["nets"]) <= names, "PCB范围包含不存在的对象")
    require(bool(scope["component_ids"] or scope["nets"]), "PCB范围不能全空")
    if plan["intent"] == "full":
        require(set(scope["component_ids"]) == set(comps) and set(scope["nets"]) == names, "整板范围须显式覆盖基线全部器件和网络")
    ops = plan["operations"]
    require(isinstance(ops, list) and len(ops) <= 200, "单批最多200个PCB操作")
    require(plan["intent"] != "advice" or not ops, "线路建议用途不能包含CAD操作")
    check(bool(ops) or (plan["intent"] == "advice" and bool(plan["recommendations"])), "缺少具体操作或线路建议")
    ids = set()
    constraints = plan["constraints"]
    require(isinstance(constraints, dict), "PCB约束必须为对象")
    if ops:
        check(snapshot.get("complete") is True and not snapshot.get("read_errors") and set(snapshot["collections"]) == set(FIELDS), "PCB图元采集不完整，不能写入")
        try:
            source_records(snapshot["source"])
            for name in ("layers", "footprints", "rules", "net_rules", "net_classes", "differential_pairs", "equal_length_groups", "origin", "drc"):
                output(snapshot, name)
            if snapshot['outputs'].get('stackup', {}).get('status') != 'returned':
                check(text(constraints.get('external_stackup_evidence')), "客户端层叠API不可用，需要独立层叠原件/板厂确认引用，不能按默认层数推定")
        except ValueError as e:
            findings.append(str(e))
        check(snapshot["identity"]["editor_version"] == "4.1.100", "当前PCB实验契约只允许4.1.100，其他版本先单独适配")
        check(constraints.get("unit") == "mil" and constraints.get("coordinate_system") == "data-origin", "约束需要mil和data-origin坐标")
        calibration = constraints.get("calibration", {})
        check(isinstance(calibration, dict) and calibration.get("editor_version") == snapshot["identity"]["editor_version"]
              and calibration.get("document_uuid") == snapshot["identity"]["document_uuid"] and text(calibration.get("evidence")), "需要本PCB坐标、旋转、层映射核验记录")
        check(text(constraints.get("stackup_evidence")) and text(constraints.get("rules_evidence")), "缺少层叠/制造规则依据")
        region = constraints.get("board_region")
        region_ok = (isinstance(region, list) and len(region) == 4 and all(number(x) for x in region)
                     and region[0] < region[2] and region[1] < region[3])
        check(region_ok, "缺少有效的板内可操作区域[xmin,ymin,xmax,ymax]")
    else:
        region_ok = False
    def within(point):
        if region_ok:
            r = constraints["board_region"]
            require(r[0] <= point[0] <= r[2] and r[1] <= point[1] <= r[3], "操作超出声明的板内区域")
    net_constraints = constraints.get("net_constraints", {})
    require(isinstance(net_constraints, dict), "net_constraints必须为对象")
    for op in ops:
        validate_operation(op)
        require(op["id"] not in ids, "PCB操作ID重复")
        ids.add(op["id"])
        kind, p = op["operation"], op["parameters"]
        affected = []
        if kind == "add_outline":
            require(plan["intent"] == "full" and len(ops) == 1, "新增板框仅限整板单独一批，之后重新采集")
            existing_outline = [v for v in source_records(snapshot["source"]).values() if v[1].get("layerId") == snapshot["layer_ids"].get("BOARD_OUTLINE")]
            require(not existing_outline, "已有板框不可直接叠加，请保留并按其约束设计")
            check(text(constraints.get("mechanical_evidence")), "板框缺少机械尺寸依据")
        elif kind == "auto_place":
            require(plan["intent"] == "full" and len(ops) == 1, "原生自动布局仅限整板单独一批，之后重新采集再布线")
            check(text(constraints.get("placement_review")), "自动布局需要机械固定件、锁定状态和分区评审依据")
            check(not any(snapshot['collections'].get(k) for k in ('Via','Pour','Poured'))
                  and not any(r['properties'].get('Net') for k in ('Line','Arc','Polyline','Fill') for r in snapshot['collections'].get(k,[])),
                  "原生自动布局不会带动已有铜线，已布线/铺铜板只能制定局部布局及重布方案")
        elif kind == "move_component":
            require(p["primitive_id"] in scope["component_ids"], "移动器件不在约定范围")
            comp = comps[p["primitive_id"]]
            require(comp["properties"].get("PrimitiveLock") is False, "不能移动锁定/未知锁状态器件")
            affected = sorted({pad["properties"].get("Net") for pad in comp.get("pads", []) if pad["properties"].get("Net")})
            require(set(affected) <= set(scope["nets"]), "器件移动涉及范围外网络，请显式纳入其布局/回流复核")
            within([p["x"], p["y"]])
        elif kind == "rebuild_pour":
            rows = [r for r in snapshot["collections"].get("Pour", []) if r["id"] == p["primitive_id"]]
            require(len(rows) == 1, "铜区不存在")
            affected = [rows[0]["properties"].get("Net")]
        elif kind == "route_nets":
            affected = p["nets"]
        elif p.get("layer") == "BOARD_OUTLINE":
            require(kind == "add_line" and p["net"] == "" and plan["intent"] == "full", "板框只能在整板模式作为无网络线段新增")
        else:
            affected = [p["net"]]
        require(set(affected) <= set(scope["nets"]), "操作涉及范围外网络")
        layer_names = ["BOARD_OUTLINE"] if kind == "add_outline" else p.get("layers", [p["layer"]] if "layer" in p else [])
        for layer_name in layer_names:
            require(layer_name in snapshot.get("layer_ids", {}), "快照未识别该图层枚举")
            actual_id = snapshot["layer_ids"][layer_name]
            check(any(v.get("id") == actual_id and v.get("layerStatus") != 0 and not v.get("locked")
                      for v in output(snapshot, "layers")), "指定图层未启用或已锁定：" + layer_name)
        for name in affected:
            rule = net_constraints.get(name)
            check(isinstance(rule, dict) and rule.get("kind") in {"power", "high-speed", "sensitive", "ordinary"}
                  and text(rule.get("source")) and text(rule.get("return_path")) and text(rule.get("validation")), "网络缺少分类、依据、返回路径或验证：" + name)
            if not isinstance(rule, dict):
                continue
            if rule.get("kind") == "power":
                details = rule.get("power_analysis", {})
                check(isinstance(details, dict) and all(text(details.get(k)) for k in
                    ("operating_conditions", "fault_and_protection", "loss_and_thermal")), "功率网络缺少工况、故障保护或损耗/热依据：" + name)
            elif rule.get("kind") == "high-speed":
                details = rule.get("signal_analysis", {})
                check(isinstance(details, dict) and all(text(details.get(k)) for k in
                    ("edge_and_topology", "impedance_and_timing", "reference_transitions")), "高速网络缺少边沿/拓扑、阻抗/时序或参考转换依据：" + name)
            elif rule.get("kind") == "sensitive":
                check(text(rule.get("coupling_review")), "敏感网络缺少耦合复核依据：" + name)
            if kind == "add_line":
                check(number(rule.get("min_width"), positive=True) and p["width"] >= rule.get("min_width", math.inf), "线宽低于约束或缺少依据：" + name)
            if kind == 'add_pour':
                check(p['width'] == 0.2 and p['priority'] == 1 and p['name'] == p['name'].upper(),
                      "4.1.100铜区仅核验默认0.2mil边框、大写名称、首个优先级1；自定义参数未兑现，拒绝执行")
                check(not any(r['properties'].get('Layer') == snapshot.get('layer_ids',{}).get(p['layer'])
                              for r in snapshot['collections'].get('Pour',[])), "4.1.100同层多铜区优先级尚未核验")
            if kind == "route_nets":
                require(rule.get("kind") == "ordinary", "高功率/高速/敏感网络必须先规划明确路径，不能交给普通自动布线")
        if kind == "add_line":
            within(p["start"]); within(p["end"])
        elif kind == "add_via":
            within([p["x"], p["y"]])
            check(number(constraints.get("min_via_hole"), positive=True) and p["hole"] >= constraints.get("min_via_hole", math.inf), "过孔孔径低于工艺约束或约束缺失")
            check(number(constraints.get("min_annular_ring"), positive=True) and (p["diameter"]-p["hole"])/2 >= constraints.get("min_annular_ring", math.inf), "过孔环宽低于工艺约束或约束缺失")
        elif kind in {"add_pour", "add_outline"}:
            for point in [p["polygon"][:2]] + [p["polygon"][i:i+2] for i in range(3, len(p["polygon"]), 2)]:
                within(point)
    return {"contract": CONTRACT, "intent": plan["intent"], "request": plan["request"],
        "plan_sha256": sha(plan), "target": {"window_id": snapshot["window_id"], **snapshot["identity"]},
        "scope": deepcopy(scope), "pin_mapping": mapping, "constraints": deepcopy(constraints),
        "operations": deepcopy(ops), "recommendations": deepcopy(plan["recommendations"]),
        "findings": findings, "ready_for_approval": not findings and bool(ops),
        "validation_plan": deepcopy(plan["validation_plan"]), "limitations": LIMITATIONS[:]}, snapshot


def drc_leaves(value):
    """Retain complete violations; unwrap the observed 4.1.100 UI tree."""
    require(isinstance(value, list), "详细PCB DRC未返回数组")
    leaves = []
    for row in value:
        if isinstance(row, dict) and 'list' in row:
            require(isinstance(row['list'], list), "详细PCB DRC分组结构不兼容")
            require(bool(row['list']) or not row.get('count'), "详细PCB DRC有计数但缺少条目，不能当作通过")
            leaves.extend(drc_leaves(row['list']))
        else:
            leaves.append(row)
    return leaves


def same_record(a, b):
    # 4.1.100 resequences logical tickets on existing records after insertion.
    # Preserve every identity/envelope field and payload except that clock.
    return ({k:v for k,v in a[0].items() if k != 'ticket'} ==
            {k:v for k,v in b[0].items() if k != 'ticket'} and a[1] == b[1])


def same_source(a, b):
    old,new=source_records(a),source_records(b)
    return list(old) == list(new) and all(same_record(old[k],new[k]) for k in old)


def polygon_signature(value):
    """Exact straight polygon boundary, allowing cycle/reversal only."""
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        value = value[0]
    require(isinstance(value, list) and len(value) >= 9 and value[2] == 'L'
            and len(value) % 2 == 1 and all(number(v) for i,v in enumerate(value) if i != 2), "直线闭合多边形格式不兼容")
    points = [tuple(value[:2])] + [tuple(value[i:i+2]) for i in range(3,len(value),2)]
    require(points[0] == points[-1], "多边形不闭合")
    points = points[:-1]
    candidates = []
    for seq in (points, list(reversed(points))):
        candidates.extend(tuple(seq[i:]+seq[:i]) for i in range(len(seq)))
    return min(candidates)


def drc_identity(row):
    # Positions and UI error indices change when an unconnected part moves.
    # Preserve object IDs, rule, net, error class, measurements and all unknown
    # fields. Unknown DRC formats remain exact rather than guessed identities.
    value = deepcopy(row)
    if isinstance(value, dict) and text(value.get('errorType')) and text(value.get('ruleName')) and string_list(value.get('objs')):
        value.pop('globalIndex', None)
        value.pop('pos', None)
        explanation = value.get('explanation')
        err = explanation.get('errData') if isinstance(explanation,dict) else None
        if isinstance(err, dict):
            err.pop('globalIndex', None)
            err.pop('position', None)
    return sha(value)


def compare(before, after):
    validate_snapshot(before); validate_snapshot(after)
    require(before["identity"] == after["identity"] and before["window_id"] == after["window_id"], "不能比较不同PCB/窗口/版本")
    old, new = source_records(before["source"]), source_records(after["source"])
    old_drc, new_drc = drc_leaves(output(before, "drc")), drc_leaves(output(after, "drc"))
    added_drc = Counter(drc_identity(v) for v in new_drc) - Counter(drc_identity(v) for v in old_drc)
    removed_drc = Counter(drc_identity(v) for v in old_drc) - Counter(drc_identity(v) for v in new_drc)
    return {"added": [list(k) for k in new if k not in old], "removed": [list(k) for k in old if k not in new],
            "changed": [list(k) for k in old if k in new and not same_record(old[k],new[k])],
            "clock_only_changes": [list(k) for k in old if k in new and old[k] != new[k] and same_record(old[k],new[k])],
            "new_drc": [v for v in new_drc if drc_identity(v) in added_drc],
            "removed_drc": [v for v in old_drc if drc_identity(v) in removed_drc],
            "drc_before": len(old_drc), "drc_after": len(new_drc),
            "source_changed": not same_source(before["source"],after["source"]),
            "raw_source_changed": before["source_sha256"] != after["source_sha256"],
            "scope": "完整PCB源记录和实际详细DRC；未推断热、SI/PI或实板性能"}


def verify_operation(before, after, op, created_id, scope, constraints=None, *, defer_drc=False):
    # A separately authorized experimental work order may inspect intermediate
    # layout/routing DRC without treating an unfinished board as a final pass.
    # Normal execution leaves this false. Raw differences remain in the report.
    report = compare(before, after)
    require(after.get("complete") is True and not after.get("read_errors"), "写后PCB读取不完整")
    old, new = source_records(before["source"]), source_records(after["source"])
    kind, p = op["operation"], op["parameters"]
    moving = {p["primitive_id"]} if kind == "move_component" else set(scope["component_ids"]) if kind == "auto_place" else set()
    moved = set()
    def is_poured(record):
        if kind != 'rebuild_pour' or record[0]['type'] != 'POURED':
            return False
        if record[1].get('targetId') == p['primitive_id']:
            return True
        try:
            return json.loads(record[0].get('id','')) == ['POURED',p['primitive_id']]
        except (ValueError,TypeError):
            return False
    for key, record in old.items():
        replacement = new.get(key)
        if is_poured(record):
            continue
        require(replacement is not None, "删除了范围外或既有PCB记录")
        if same_record(record, replacement):
            continue
        env, payload = record
        env2, payload2 = replacement
        coordinate_keys = ('x','y') if 'x' in payload and 'y' in payload else ('positionX','positionY')
        require(not ({'x','y','positionX','positionY'} <= set(payload)), "PCB几何含两种冲突坐标字段")
        geometric = {*coordinate_keys, "angle"}
        movable = env.get("type") == "COMPONENT" and env.get("id") in moving
        attribute = env.get("type") == "ATTR" and payload.get("parentId") in moving
        require(movable or attribute, "改变了方案外PCB记录：" + str(key))
        require({k:v for k,v in env.items() if k != "ticket"} == {k:v for k,v in env2.items() if k != "ticket"}, "PCB源记录身份发生变化")
        require({k:v for k,v in payload.items() if k not in geometric} == {k:v for k,v in payload2.items() if k not in geometric}, "器件或属性发生非几何修改")
        if movable:
            comp = components(before).get(env["id"])
            require(comp and comp["properties"].get("PrimitiveLock") is False, "自动布局移动了锁定/未知器件")
            moved.add(env["id"])
    for key in new.keys() - old.keys():
        env, payload = new[key]
        if is_poured(new[key]):
            continue
        if kind == "route_nets":
            require(env["type"] in {"LINE", "ARC", "VIA", "POLY"} and payload.get("netName") in p["nets"], "自动布线新增了范围外图元/网络")
            if env["type"] != "VIA":
                require(payload.get("layerId") in [before["layer_ids"][n] for n in p["layers"]], "自动布线超出允许层")
        else:
            expected = {"add_line": "LINE", "add_via": "VIA", "add_pour": "POUR", "add_outline": "POLY"}.get(kind)
            require(expected and env["type"] == expected and env.get("id") == created_id, "新增了方案外PCB记录")
    # Record order can encode rule and copper priority. Preserve unaffected order.
    preserved = [k for k in old if not is_poured(old[k])]
    require(preserved == [k for k in new if k in old and not is_poured(old[k])], "既有PCB记录顺序改变")
    if kind == "move_component":
        comp = components(after).get(p["primitive_id"], {}).get("properties", {})
        require(all(number(comp.get(k)) and math.isclose(comp[k],p[v],rel_tol=0,abs_tol=1e-8)
                    for k, v in (("X","x"),("Y","y"),("Rotation","rotation"))), "器件位置/旋转回读不一致")
    for cid in moved:
        values = [v for e,v in new.values() if e["type"] == "COMPONENT" and e.get("id") == cid]
        props = components(after)[cid]["properties"]
        if len(values) == 1:
            xy = ('x','y') if 'x' in values[0] and 'y' in values[0] else ('positionX','positionY')
        require(len(values) == 1 and all(number(values[0].get(k)) and number(props.get(v))
            and math.isclose(values[0][k],props[v],rel_tol=0,abs_tol=0.000050001 if k in xy else 1e-8)
            for k,v in ((xy[0],"X"),(xy[1],"Y"),("angle","Rotation"))), "器件源记录与API几何回读不一致")
    if kind in {"add_line", "add_via", "add_pour", "add_outline"}:
        category = {"add_line":"Line", "add_via":"Via", "add_pour":"Pour", "add_outline":"Polyline"}[kind]
        rows = [r for r in after["collections"][category] if r["id"] == created_id]
        require(len(rows) == 1, "新增PCB图元无法独立回读")
        require(not any(r["id"] == created_id for r in before["collections"][category]), "创建返回了已有PCB对象，不能当作新增成功")
        props = rows[0]["properties"]
        expected = {"Net": p.get("net", "")}
        if kind == 'add_outline':
            require(props.get('Net') in (None,''), "板框不能分配到电气网络")
            expected = {}
        if kind == "add_line":
            expected.update(Layer=before["layer_ids"][p["layer"]], StartX=p["start"][0], StartY=p["start"][1], EndX=p["end"][0], EndY=p["end"][1], LineWidth=p["width"])
        elif kind == "add_via":
            expected.update(X=p["x"], Y=p["y"], HoleDiameter=p["hole"], Diameter=p["diameter"])
        elif kind == "add_pour":
            require(polygon_signature(props.get('ComplexPolygon')) == polygon_signature(p['polygon']), "铜区轮廓回读不一致")
            expected.update(Layer=before["layer_ids"][p["layer"]], PourName=p["name"], PourPriority=p["priority"], LineWidth=p["width"], PreserveSilos=False, PourFillMethod="solid")
        else:
            require(polygon_signature(props.get('Polygon')) == polygon_signature(p['polygon']), "板框轮廓回读不一致")
            expected.update(Layer=before["layer_ids"]["BOARD_OUTLINE"], LineWidth=p["width"])
        require(all(math.isclose(props.get(k),v,rel_tol=0,abs_tol=1e-8) if number(v) and number(props.get(k)) else props.get(k)==v for k,v in expected.items()), "新增PCB图元参数与方案不一致")
        values = [v for e,v in new.values() if e.get("id") == created_id
                  and e["type"] == {"add_line":"LINE","add_via":"VIA","add_pour":"POUR","add_outline":"POLY"}[kind]]
        require(len(values) == 1, "新增PCB源记录缺失或重复")
        value = values[0]
        require(value.get("netName") == p.get("net", ""), "PCB源记录网络与API回读不一致")
        if kind == "add_line":
            expected_source = {"layerId":expected["Layer"], "startX":p["start"][0], "startY":p["start"][1],
                               "endX":p["end"][0], "endY":p["end"][1], "width":p["width"]}
        elif kind == "add_via":
            require(value.get('viaType') in (0,'NORMAL') and props.get('ViaType') == 0, "过孔源类型不是已核验的普通通孔")
            expected_source = {"centerX":p["x"], "centerY":p["y"], "holeDiameter":p["hole"], "viaDiameter":p["diameter"]}
        else:
            expected_source = {"layerId":expected["Layer"], "width":p["width"]}
            require(polygon_signature(value.get('path')) == polygon_signature(p['polygon']), "PCB源多边形与API回读不一致")
            if kind == "add_pour":
                expected_source.update(name=p["name"], order=p["priority"])
        require(all(value.get(k) == v for k,v in expected_source.items()), "PCB源记录几何与API回读不一致")
    # All pad-net assignments must remain exact, even after native placement.
    def pin_map(s):
        return {cid: sorted((p["properties"].get("PadNumber"), p["properties"].get("Net"))
                            for p in c.get("pads", [])) for cid, c in components(s).items()}
    require(pin_map(before) == pin_map(after), "PCB焊盘网络或器件集合改变")
    for name in ("layers", "footprints", "stackup", "origin", "rules", "net_rules", "net_classes", "differential_pairs", "equal_length_groups", "board"):
        require(before['outputs'].get(name) == after['outputs'].get(name), "写入改变了方案外PCB配置：" + name)
    require(defer_drc or not report["new_drc"], "写后出现新的/无法匹配的详细PCB DRC，停止并保留现场")
    if constraints:
        x1, y1, x2, y2 = constraints["board_region"]
        def within(x, y):
            require(number(x) and number(y) and x1 <= x <= x2 and y1 <= y <= y2, "写后几何超出声明的板内区域")
        for cid in moved:
            row = components(after)[cid]
            within(row["properties"].get("X"), row["properties"].get("Y"))
            bbox = row.get("bbox")
            require(isinstance(bbox, dict), "写后器件外形包络不可用")
            within(bbox.get("minX"), bbox.get("minY")); within(bbox.get("maxX"), bbox.get("maxY"))
        if kind == "route_nets":
            for category in ("Line", "Arc", "Via", "Polyline"):
                old_ids = {r["id"] for r in before["collections"][category]}
                for row in after["collections"][category]:
                    if row["id"] in old_ids:
                        continue
                    props = row["properties"]
                    require(props.get("Net") in p["nets"], "自动布线回读包含范围外网络")
                    if category == "Via":
                        within(props.get("X"), props.get("Y"))
                        require(number(props.get("HoleDiameter"), positive=True)
                            and props["HoleDiameter"] >= constraints.get("min_via_hole", math.inf), "自动布线过孔孔径低于约束")
                        require(number(props.get("Diameter"), positive=True)
                            and (props["Diameter"]-props["HoleDiameter"])/2 >= constraints.get("min_annular_ring", math.inf), "自动布线过孔环宽低于约束")
                    else:
                        require(props.get("Layer") in [before["layer_ids"][n] for n in p["layers"]], "自动布线回读层超出范围")
                        rule = constraints["net_constraints"][props["Net"]]
                        require(number(props.get("LineWidth"), positive=True)
                            and props["LineWidth"] >= rule.get("min_width", math.inf), "自动布线线宽低于网络约束")
                        if category == "Line":
                            within(props.get("StartX"), props.get("StartY")); within(props.get("EndX"), props.get("EndY"))
                        else:
                            # Native engine may emit arcs/polylines; their full
                            # curves still need the CAD region/DRC review.
                            require(text(rule.get("curve_review")), "自动布线产生曲线/多段线，需要明确的几何复核依据")
    return {**report, "drc_deferred": defer_drc, "moved_component_ids": sorted(moved), "verified_scope": "声明的PCB操作及完整源差异，非工程性能验收"}
