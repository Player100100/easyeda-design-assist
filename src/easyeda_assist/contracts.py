"""Small versioned contracts; shape checks do not certify engineering claims."""
from __future__ import annotations

MODES = {
    "datasheet": ("手册要点", ["完整MPN/封装后缀", "手册原文/修订", "目标工作条件"]),
    "selection": ("关键器件选型", ["电压/电流/温度工况", "封装/成本约束", "供货与替代要求"]),
    "topology": ("功能拓扑", ["输入输出规格", "负载/频带/精度", "保护与故障工况"]),
    "schematic": ("原理图局部建议", ["目标位号/网络与关联连接", "器件手册", "保护需求/接口对端"]),
    "pcb": ("PCB布线与铺铜建议", ["目标网络/元件及实际几何", "板层/叠层/规则", "电流/速率/回流要求"]),
    "local-check": ("局部修改检查", ["改前/改后版本", "修改对象与关联网络", "预期行为和验证要求"]),
}
TASK_STATES = {"draft", "analyzing", "needs_input", "ready_for_user", "accepted", "rejected", "deferred"}


def require(condition, message: str):
    if not condition:
        raise ValueError(message)


def nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def strings(value) -> bool:
    return isinstance(value, list) and all(nonempty(v) for v in value)


def validate_context(context):
    require(isinstance(context, dict) and context.get("schema_version") in {1, 2}, "上下文 schema_version 必须为 1 或 2")
    require(context.get("source") in {"live-api", "fixture", "manual"}, "上下文来源无效")
    require(nonempty(context.get("captured_at")), "上下文缺少采集时间")
    for field in ("project", "document"):
        require(isinstance(context.get(field), dict) and nonempty(context[field].get("uuid")), f"上下文缺少 {field}.uuid")
    domain = context.get("domain")
    require(domain in {"schematic", "pcb"}, "上下文只支持 schematic/pcb")
    require(context["document"].get("documentType") == {"schematic": 1, "pcb": 3}[domain], "文档类型与 domain 不一致")
    owner = context["document"].get("parentProjectUuid")
    require(owner is None or owner == context["project"]["uuid"], "文档归属与当前工程不一致")
    require(strings(context.get("selected_ids")), "selected_ids 必须是字符串数组，可为空")
    require(len(context["selected_ids"]) == len(set(context["selected_ids"])), "selected_ids 不允许重复")
    if context["schema_version"] == 2:
        primitives = context.get("selected_primitives")
        require(isinstance(primitives, list), "selected_primitives 必须是数组")
        require(context.get("selection_details_status") in {"captured", "unavailable", "id_mismatch", "truncated"},
                "selection_details_status 无效")
        total = context.get("selected_total")
        require(total is None or type(total) is int and total >= 0, "selected_total 必须为非负整数或 null")
        error = context.get("selection_details_error")
        require(error is None or nonempty(error), "selection_details_error 必须为非空字符串或 null")
        primitive_ids = []
        for primitive in primitives:
            require(isinstance(primitive, dict), "所选图元详情必须为对象")
            require(nonempty(primitive.get("primitive_id")), "所选图元详情缺少 primitive_id")
            require(primitive["primitive_id"] in context["selected_ids"], "所选图元详情 ID 不在 selected_ids 中")
            require(isinstance(primitive.get("properties"), dict), "图元 properties 必须为对象")
            require(strings(primitive.get("read_errors")), "图元 read_errors 必须是字符串数组")
            require(strings(primitive.get("missing_fields")), "图元 missing_fields 必须是字符串数组")
            require(type(primitive.get("supported")) is bool, "图元 supported 必须为布尔值")
            require(nonempty(primitive["properties"].get("primitive_type")), "图元缺少 primitive_type")
            primitive_ids.append(primitive["primitive_id"])
        require(len(primitive_ids) == len(set(primitive_ids)), "所选图元详情 ID 重复")
        if context["selection_details_status"] == "captured":
            require(set(primitive_ids) == set(context["selected_ids"]) and total == len(primitive_ids),
                    "captured 详情必须覆盖全部 selected_ids 且数量一致")
            require(error is None, "captured 状态不应携带详情错误")
        if context["selection_details_status"] == "unavailable":
            require(not primitives and total is None and nonempty(error), "不可用的图元详情不可携带部分快照且应说明原因")
        if context["selection_details_status"] == "truncated":
            require(bool(primitive_ids) and total == len(context["selected_ids"]) and total > len(primitive_ids)
                    and error is None,
                    "truncated 状态与图元数量不符")
        if context["selection_details_status"] == "id_mismatch":
            require(not primitives and nonempty(error), "ID 不一致时应丢弃详情并记录原因")
    require(strings(context.get("limitations")), "上下文必须记录 limitations 数组")


def validate_task(task):
    require(isinstance(task, dict) and task.get("schema_version") == 1, "任务 schema_version 必须为 1")
    for key in ("id", "goal", "created_at"):
        require(nonempty(task.get(key)), f"任务缺少 {key}")
    require(task.get("mode") in MODES, "未知任务模式")
    require(task.get("status") in TASK_STATES, "未知任务状态")
    for key in ("constraints", "missing_inputs"):
        require(strings(task.get(key)), f"{key} 必须是字符串数组")
    context = task.get("context")
    if context is not None:
        validate_context(context)
        expected = {"schematic": "schematic", "pcb": "pcb"}.get(task["mode"])
        require(expected is None or context["domain"] == expected, "任务模式与文档类型不匹配")
    sources = task.get("sources")
    require(isinstance(sources, list), "sources 必须是数组")
    source_ids = set()
    for source in sources:
        require(isinstance(source, dict), "来源必须是对象")
        for key in ("id", "kind", "reference", "locator", "description"):
            require(nonempty(source.get(key)), f"来源缺少 {key}")
        require(source["id"] not in source_ids, "来源 ID 重复")
        source_ids.add(source["id"])
    recommendations = task.get("recommendations")
    require(isinstance(recommendations, list), "recommendations 必须是数组")
    recommendation_ids = set()
    for item in recommendations:
        require(isinstance(item, dict), "建议必须是对象")
        for key in ("id", "locator", "statement", "rationale"):
            require(nonempty(item.get(key)), f"建议缺少 {key}")
        require(item["id"] not in recommendation_ids, "建议 ID 重复")
        recommendation_ids.add(item["id"])
        for key in ("evidence", "applicability", "assumptions", "risks", "validation"):
            require(strings(item.get(key)), f"建议 {key} 必须是字符串数组")
        require(bool(item["applicability"]) and bool(item["risks"]) and bool(item["validation"]), "建议必须说明适用条件、风险边界和验证动作")
        require(item.get("status") in {"suggested", "needs_input", "verified"}, "建议状态无效")
        require(item.get("confidence") in {"low", "medium", "high"}, "建议置信度无效")
        require(item.get("decision") in {"pending", "accepted", "rejected", "deferred"}, "用户决定无效")
        require(set(item["evidence"]) <= source_ids, "建议引用了不存在的 source ID")
        require(item["status"] == "needs_input" or bool(item["evidence"]), "工程建议必须引用来源；待补信息可用 needs_input")
        if item["decision"] != "pending":
            require(nonempty(item.get("decision_record")), "已记录用户决定但缺少用户原话/确认记录")
    if task["status"] in {"ready_for_user", "accepted"}:
        require(bool(recommendations), "没有建议，不能标为 ready_for_user/accepted")
        require(not task["missing_inputs"], "关键输入未补齐，使用 needs_input/analyzing")
    if task["status"] == "accepted":
        require(any(r["decision"] == "accepted" for r in recommendations), "任务采纳状态缺少已采纳建议")
