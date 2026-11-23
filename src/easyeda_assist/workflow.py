"""Independent interaction intent and analysis scope; never grants CAD access."""
from __future__ import annotations

INTERACTIONS = {"advice": "辅助绘制", "collaborative": "协同绘制"}
SCOPES = ("component", "module", "board", "product")
STEPS = ("目标与边界", "方案与电路", "PCB 与投板", "上电与联调", "验证与改版")
SIGNALS = {
    "unclear_module_boundary": ("board", "模块边界尚未确定"),
    "shared_power": ("board", "共享供电或回流条件影响多个模块"),
    "shared_timing": ("board", "共享时钟、复位或启动时序需要整板核对"),
    "budget_conflict": ("board", "功耗、热、成本或尺寸预算存在跨模块冲突"),
    "cross_board": ("product", "涉及多个物理板卡实例及其端到端连接"),
}
QUERY_HINTS = {
    "identity": "查询目标器件与库身份",
    "geometry": "查询引脚几何与符号边界",
    "connectivity": "读取网表并定点扩展关联网络",
    "ratings": "核对厂商手册并按实际工况计算或测量",
    "capability": "检查当前客户端、操作与适配器能力",
    "use_case": "询问现有资料无法确定的用途、工况或关键取舍",
}


def context(interaction="advice", scope="module", signals=(), missing=()):
    if not isinstance(interaction, str) or interaction not in INTERACTIONS:
        raise ValueError("interaction 必须为 advice/collaborative")
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ValueError("analysis_scope 必须为 component/module/board/product")
    for values, allowed, label in ((signals, SIGNALS, "scope_signals"), (missing, QUERY_HINTS, "missing_facts")):
        if not isinstance(values, (list, tuple)) or any(not isinstance(x, str) or x not in allowed for x in values):
            raise ValueError(label + " 包含未知项目")
        if len(values) != len(set(values)):
            raise ValueError(label + " 不得重复")
    effective = max((scope, *(SIGNALS[x][0] for x in signals)), key=SCOPES.index)
    return {"interaction": interaction, "analysis_scope": effective,
            "scope_signals": list(signals), "missing_facts": list(missing)}


def validate_workflow(record):
    if not isinstance(record, dict) or set(record) != {"interaction", "analysis_scope", "scope_signals", "missing_facts"}:
        raise ValueError("workflow 必须包含 interaction、analysis_scope、scope_signals、missing_facts")
    expected = context(record["interaction"], record["analysis_scope"], record["scope_signals"], record["missing_facts"])
    if expected != record:
        raise ValueError("analysis_scope 未覆盖已登记的范围提升信号")


def workflow_guide(interaction="advice", scope="module", signals=(), missing=()):
    state = context(interaction, scope, signals, missing)
    return {**state, "interaction_name": INTERACTIONS[interaction], "steps": list(STEPS),
            "scope_reasons": [SIGNALS[x][1] for x in signals],
            "next_queries": [QUERY_HINTS[x] for x in missing], "cad_write_authorized": False,
            "guidance": ["采用能闭合当前问题的最小范围，并保留上一级接口上下文。",
                         "简单咨询可直接回答；持续设计再复用项目或任务档案。",
                         "只补会影响当前决定的缺项，独立工作继续推进。",
                         "协同绘制先细化具体方案；有效认可范围内连续执行，变化时复核受影响部分。",
                         "分析范围提升不扩大绘图授权；模式不代表接口能力或工程验证通过。"]}
