from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bridge import BridgeError, discover
from .connection import connect as connect_api
from .contracts import MODES, validate_context, validate_task
from .datasheets import import_datasheet, read_page, search_document
from .drawing import new_drawing_plan, review_drawing_plan
from .drawing_execution import approve_plan, capture_snapshot, execute_plan, preflight
from .drawing_circuit import CONTRACT, audit_circuit, validate_circuit
from .drawing_api import validate_snapshot
from .source_validation import prepare_validation, check_validation, check_roundtrip
from .source_trial import execute_trial
from .drawing_properties import ALLOWED as PROPERTY_KEYS
from .guidance import guide
from .project import (PROJECT_STATES, new_project, validate_project, stage_guide,
                      project_review, project_impact, revise_project)
from .schematic import neighborhood
from .storage import digest, external, initialize, new_id, now, read_json, write_new
from .tasks import create_task, handoff
from .workflow import INTERACTIONS, SCOPES, SIGNALS, QUERY_HINTS, workflow_guide
from .operations import capabilities
from .queries import QUERY_NAMES
from .traceability import plan_trace
from .product import review_product
from .design_audit import compare_files
from .feedback import drawing_feedback
from .pcb import INTENTS as PCB_INTENTS, inspect as inspect_pcb, new_plan as new_pcb_plan
from .pcb_execution import approve as approve_pcb, execute as execute_pcb, review as review_pcb, export_files as export_pcb_files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EasyEDA 设计助手；固定查询及方案认可后的受限绘图")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化外部数据目录")
    sub.add_parser("modes", help="列出任务模式和所需输入")
    workflow = sub.add_parser("workflow", help="轻量交互模式、分析尺度与五步研发循环，不建档或写CAD")
    workflow.add_argument("--interaction", choices=tuple(INTERACTIONS), default="advice")
    workflow.add_argument("--scope", choices=SCOPES, default="module")
    workflow.add_argument("--signal", choices=tuple(SIGNALS), action="append", default=[])
    workflow.add_argument("--missing", choices=tuple(QUERY_HINTS), action="append", default=[])
    capability = sub.add_parser("capabilities", help="列出源码固定的操作实现、版本及实测边界")
    capability.add_argument("--editor-version")
    pcb_capture = sub.add_parser("pcb-capture", help="只读归档完整PCB图元、层叠、网络、规则和详细DRC")
    pcb_capture.add_argument("--port", type=int)
    pcb_capture.add_argument("--window-id")
    pcb_capture.add_argument("--without-drc", action="store_true")
    pcb_workflow = sub.add_parser("pcb-workflow", help="查看原理图完成后的四种PCB协作流程")
    pcb_workflow.add_argument("--intent", choices=tuple(PCB_INTENTS), required=True)
    pcb_compare = sub.add_parser("compare-pcb", help="比较同一PCB完整源及实际详细DRC")
    pcb_compare.add_argument("before", type=Path)
    pcb_compare.add_argument("after", type=Path)
    pcb_plan = sub.add_parser("new-pcb-plan", help="根据已完成原理图和PCB基线创建PCB绘制计划")
    pcb_plan.add_argument("snapshot", type=Path, help="pcb-capture生成的快照")
    pcb_plan.add_argument("schematic", type=Path, help="schematic --save生成的原理图网表")
    pcb_plan.add_argument("--intent", choices=tuple(PCB_INTENTS), required=True)
    pcb_plan.add_argument("--request", required=True)
    pcb_plan.add_argument("--component-id", action="append", default=[])
    pcb_plan.add_argument("--net", action="append", default=[])
    pcb_review = sub.add_parser("review-pcb-plan", help="离线检查PCB方案范围、引脚映射和固定操作")
    pcb_review.add_argument("plan", type=Path)
    pcb_approve = sub.add_parser("approve-pcb-plan", help="记录PCB方案的真实用户认可，不调用CAD")
    pcb_approve.add_argument("plan", type=Path)
    pcb_approve.add_argument("--shown-plan-sha256", required=True)
    pcb_approve.add_argument("--user-message", required=True)
    pcb_approve.add_argument("--reference", required=True)
    pcb_execute = sub.add_parser("execute-pcb-plan", help="按认可方案执行一次固定PCB操作并回读")
    pcb_execute.add_argument("plan", type=Path)
    pcb_execute.add_argument("approval", type=Path)
    pcb_execute.add_argument("--port", type=int)
    pcb_execute.add_argument("--window-id", help="必须与计划基线的窗口一致")
    pcb_execute.add_argument("--experimental-pcb", action="store_true", help="允许尚未完成本项目实机验收的PCB适配器执行")
    pcb_inspect = sub.add_parser("inspect-pcb", help="离线查询PCB快照中的器件、网络和已布图元")
    pcb_inspect.add_argument("snapshot", type=Path)
    target_pcb = pcb_inspect.add_mutually_exclusive_group()
    target_pcb.add_argument("--net")
    target_pcb.add_argument("--component-id")
    pcb_export = sub.add_parser("export-pcb", help="从当前PCB导出制造文件原件，绑定工程与完整源")
    pcb_export.add_argument("--port", type=int)
    pcb_export.add_argument("--window-id")
    query_service = sub.add_parser("query", help="通过统一入口执行固定只读查询并保留来源")
    query_service.add_argument("--kind", choices=QUERY_NAMES, required=True)
    query_service.add_argument("--primitive-id")
    query_service.add_argument("--port", type=int)
    query_service.add_argument("--window-id")
    query_service.add_argument("--save", action="store_true")
    product_review = sub.add_parser("product-review", help="离线核对板卡实例与显式端到端引脚连接表")
    product_review.add_argument("product", type=Path)
    trace = sub.add_parser("drawing-trace", help="追踪计划的建议/决策来源、失效项和执行结果")
    trace.add_argument("project", type=Path)
    trace.add_argument("plan", type=Path)
    audit_diff = sub.add_parser("compare-design-audits", help="离线比较同目标审计的引脚网络与详细DRC；不证明读模型新鲜度")
    audit_diff.add_argument("before", type=Path)
    audit_diff.add_argument("after", type=Path)
    audit_diff.add_argument("--save", action="store_true")
    feedback = sub.add_parser("drawing-feedback", help="从执行结果生成独立的后续核验与受影响对象记录")
    feedback.add_argument("project", type=Path)
    feedback.add_argument("plan", type=Path)
    feedback.add_argument("--comparison", type=Path, help="compare-design-audits --save 归档的report.json")
    validation = sub.add_parser("prepare-source-validation", help="离线归档单属性实机验证候选方案，不认可或执行CAD修改")
    validation.add_argument("source", type=Path, help="source-component统一查询")
    validation.add_argument("audit", type=Path, help="同源状态的audit查询")
    validation.add_argument("--property", choices=sorted(PROPERTY_KEYS), required=True)
    validation.add_argument("--value", required=True)
    validation_check = sub.add_parser("check-source-validation", help="只读核对属性在源和网表中的变化；DRC新鲜度仍未知")
    validation_check.add_argument("case", type=Path)
    validation_check.add_argument("source_after", type=Path)
    validation_check.add_argument("audit_after", type=Path)
    validation_check.add_argument("--save", action="store_true")
    roundtrip = sub.add_parser("check-source-roundtrip", help="离线核对已声明的属性/DRC实验响应及完整恢复，不修改原执行结果")
    roundtrip.add_argument("case", type=Path)
    roundtrip.add_argument("trial_source", type=Path)
    roundtrip.add_argument("trial_audit", type=Path)
    roundtrip.add_argument("restored_source", type=Path)
    roundtrip.add_argument("restored_audit", type=Path)
    roundtrip.add_argument("--expected-drc-added", type=Path, help="实验前声明的详细DRC新增条目JSON数组；默认无新增")
    roundtrip.add_argument("--save", action="store_true")
    trial = sub.add_parser("execute-source-trial", help="按明确测试授权运行一次绑定的单属性实验；正式调度保持关闭")
    trial.add_argument("case", type=Path)
    trial.add_argument("--shown-sha256", required=True)
    trial.add_argument("--user-message", required=True)
    trial.add_argument("--reference", required=True)
    trial.add_argument("--reviewed-lock-sha256")
    trial.add_argument("--backend", choices=("source", "canvas-attribute"), default="canvas-attribute")
    trial.add_argument("--port", type=int)
    project = sub.add_parser("new-project", help="创建离线项目设计档案初稿")
    project.add_argument("--name", required=True)
    project.add_argument("--goal", required=True)
    project.add_argument("--interaction", choices=tuple(INTERACTIONS), default="advice")
    project.add_argument("--scope", choices=SCOPES, default="board")
    check_project = sub.add_parser("validate-project", help="检查项目档案结构、引用和任务依赖")
    check_project.add_argument("project", type=Path)
    drawing = sub.add_parser("new-drawing-plan", help="离线创建绘图计划初稿，不执行 CAD 操作")
    drawing.add_argument("project", type=Path)
    drawing.add_argument("--request", required=True)
    drawing.add_argument("--task", type=Path)
    drawing.add_argument("--recommendation-id", action="append", default=[])
    drawing.add_argument("--decision-id", action="append", default=[])
    drawing_review = sub.add_parser("review-drawing-plan", help="检查计划记录缺项与项目策略")
    drawing_review.add_argument("project", type=Path)
    drawing_review.add_argument("plan", type=Path)
    drawing_source = sub.add_parser("drawing-snapshot", help="只读归档当前原理图完整文档源，供绘图方案绑定")
    drawing_source.add_argument("--port", type=int)
    drawing_source.add_argument("--window-id")
    incremental_baseline = sub.add_parser("capture-incremental-baseline", help="只读归档非空白页完整审计、器件清单及全部引脚几何")
    incremental_baseline.add_argument("--port", type=int)
    incremental_baseline.add_argument("--window-id")
    drawing_svg = sub.add_parser("drawing-svg", help="只读导出当前原理图原生SVG供视觉审核")
    drawing_svg.add_argument("--port", type=int)
    drawing_svg.add_argument("--window-id")
    visual = sub.add_parser("review-drawing-visual", help="离线筛查原生SVG中可能遮挡文字的导线")
    visual.add_argument("after", type=Path, help="drawing-svg 归档的证据 JSON")
    visual.add_argument("--before", type=Path, help="同一原理图页修改前的 SVG 证据 JSON")
    visual.add_argument("--save", action="store_true")
    circuit_audit = sub.add_parser("audit-drawing-circuit", help="只读核对已完成电路的最终网表与详细DRC")
    circuit_audit.add_argument("snapshot", type=Path)
    circuit_audit.add_argument("plan", type=Path)
    circuit_audit.add_argument("--port", type=int)
    drawing_components = sub.add_parser("drawing-components", help="只读归档当前页器件库身份及放置属性，供准备方案")
    drawing_components.add_argument("--port", type=int)
    drawing_components.add_argument("--window-id")
    drawing_pins = sub.add_parser("drawing-pins", help="只读归档单个画布器件的引脚编号、名称与原始坐标")
    drawing_pins.add_argument("--primitive-id", required=True)
    drawing_pins.add_argument("--port", type=int)
    drawing_pins.add_argument("--window-id")
    drawing_project = sub.add_parser("drawing-project-info", help="只读归档当前工程文件配置及库来源元数据，不保存CAD")
    drawing_project.add_argument("--port", type=int)
    drawing_project.add_argument("--window-id")
    relocate = sub.add_parser("relocate-drawing-component", help="核验后移动指定原理图器件并修正位号")
    relocate.add_argument("snapshot", type=Path)
    relocate.add_argument("--primitive-id", required=True)
    relocate.add_argument("--project-device-uuid", required=True)
    relocate.add_argument("--from-x", type=float, required=True)
    relocate.add_argument("--from-y", type=float, required=True)
    relocate.add_argument("--from-designator", required=True)
    relocate.add_argument("--to-x", type=float, required=True)
    relocate.add_argument("--to-y", type=float, required=True)
    relocate.add_argument("--to-designator", required=True)
    relocate.add_argument("--port", type=int)
    relocate.add_argument("--window-id")
    drawing_search = sub.add_parser("drawing-device-search", help="只读搜索系统器件库，归档最多十条候选，不自动选型")
    drawing_search.add_argument("--keyword", required=True)
    drawing_search.add_argument("--port", type=int)
    drawing_search.add_argument("--window-id")
    drawing_details = sub.add_parser("drawing-device-details", help="只读归档指定库器件属性及子部件名称")
    drawing_details.add_argument("--library-uuid", required=True)
    drawing_details.add_argument("--device-uuid", required=True)
    drawing_details.add_argument("--port", type=int)
    drawing_details.add_argument("--window-id")
    archive = sub.add_parser("drawing-device-archive", help="只读归档库器件 elibz2，供核对符号和引脚几何")
    archive.add_argument("--library-uuid", required=True)
    archive.add_argument("--device-uuid", required=True)
    archive.add_argument("--port", type=int)
    archive.add_argument("--window-id")
    for name in ("prepare-drawing-execution", "approve-drawing-plan", "execute-drawing-plan"):
        command = sub.add_parser(name, help={
            "prepare-drawing-execution": "离线核验可执行参数和快照，输出应向用户展示的具体方案",
            "approve-drawing-plan": "记录真实对话中的方案认可；不会调用 CAD",
            "execute-drawing-plan": "消费一次认可，执行受限原理图绘制并留存逐步日志"}[name])
        command.add_argument("project", type=Path)
        command.add_argument("plan", type=Path)
        if name == "approve-drawing-plan":
            command.add_argument("--shown-plan-sha256", required=True)
            command.add_argument("--user-message", required=True)
            command.add_argument("--reference", required=True)
        if name == "execute-drawing-plan":
            command.add_argument("approval", type=Path)
            command.add_argument("--port", type=int)
    project_guidance = sub.add_parser("project-guide", help="读取项目级阶段指引")
    project_guidance.add_argument("--stage", choices=sorted(PROJECT_STATES), required=True)
    review = sub.add_parser("project-review", help="报告项目档案缺项、任务依赖和关联任务状态")
    review.add_argument("project", type=Path)
    for name in ("project-impact", "project-revise"):
        command = sub.add_parser(name, help="比较显式依赖影响" if name == "project-impact" else "保留原记录并归档项目修订")
        command.add_argument("previous", type=Path)
        command.add_argument("current", type=Path)
        if name == "project-revise":
            command.add_argument("--reason", required=True)
    guidance = sub.add_parser("guide", help="读取 agent 工程工作指引")
    guidance.add_argument("--mode", choices=tuple(MODES), required=True)
    connect = sub.add_parser("connect", help="启动 bridge 并等待嘉立创 EDA API Gateway 接入")
    connect.add_argument("--timeout", type=float, default=60, help="等待客户端秒数，0 表示只启动 bridge")
    connect.add_argument("--skill-dir", type=Path, help="已安装 easyeda-api-skill 目录")
    connect.add_argument("--window-id", help="等待指定 EDA 窗口")
    for name in ("status", "context", "schematic"):
        command = sub.add_parser(name)
        command.add_argument("--port", type=int, help="本机 bridge 端口；默认扫描 49620–49629")
        if name != "status":
            command.add_argument("--window-id", help="多个窗口时必须明确指定")
            command.add_argument("--save", action="store_true", help="归档上下文并输出路径")
    query = sub.add_parser("inspect", help="按位号/网表元件键/网络查询同一次网表导出的关联连接")
    query.add_argument("snapshot", type=Path)
    target = query.add_mutually_exclusive_group(required=True)
    target.add_argument("--designator")
    target.add_argument("--component-id")
    target.add_argument("--net")
    query.add_argument("--depth", type=int, default=1)
    query.add_argument("--limit", type=int, default=100)
    task = sub.add_parser("new-task", help="生成待分析任务，不自动生成工程建议")
    task.add_argument("--mode", choices=tuple(MODES), required=True)
    task.add_argument("--goal", required=True)
    task.add_argument("--constraint", action="append", default=[])
    task.add_argument("--interaction", choices=tuple(INTERACTIONS), default="advice")
    task.add_argument("--scope", choices=SCOPES, default="module")
    task.add_argument("--signal", choices=tuple(SIGNALS), action="append", default=[])
    task.add_argument("--missing", choices=tuple(QUERY_HINTS), action="append", default=[])
    inputs = task.add_mutually_exclusive_group()
    inputs.add_argument("--context", type=Path, help="已归档/手动输入的 context JSON")
    inputs.add_argument("--live", action="store_true", help="从已连接的客户端采集上下文")
    task.add_argument("--port", type=int)
    task.add_argument("--window-id")
    for name in ("validate-task", "handoff"):
        command = sub.add_parser(name)
        command.add_argument("task", type=Path)
    datasheet = sub.add_parser("import-datasheet", help="保留手册原件与文字索引，供按问题查询")
    datasheet.add_argument("task", type=Path)
    datasheet.add_argument("file", type=Path)
    datasheet.add_argument("--manufacturer")
    datasheet.add_argument("--mpn")
    datasheet.add_argument("--primitive-id", help="上下文有多个元件时指定目标图元 ID")
    datasheet.add_argument("--revision", help="声明的手册修订；导入后仍须对照原件核对")
    catalog = sub.add_parser("datasheets", help="列出任务可访问的手册清单")
    catalog.add_argument("task", type=Path)
    search = sub.add_parser("datasheet-search", help="按具体问题的关键词查询手册")
    search.add_argument("manifest", type=Path, help="datasheet.json 访问清单")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)
    page = sub.add_parser("datasheet-page", help="查阅手册完整页，可渲染 PDF 原页")
    page.add_argument("manifest", type=Path)
    page.add_argument("--page", type=int, required=True)
    page.add_argument("--render", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = {"data_root": str(initialize())}
        elif args.command == "modes":
            result = {k: {"name": v[0], "required_inputs": v[1]} for k, v in MODES.items()}
        elif args.command == "workflow":
            result = workflow_guide(args.interaction, args.scope, args.signal, args.missing)
        elif args.command == "capabilities":
            result = capabilities(args.editor_version)
        elif args.command == "pcb-capture":
            record = discover(args.port).pcb_capture(args.window_id, with_drc=not args.without_drc)
            result = {"snapshot_path": str(write_new(initialize() / "pcb-snapshots" /
                              (new_id("pcb") + ".json"), record)),
                      "source_sha256": record["source_sha256"], "complete": record["complete"],
                      "read_errors": record["read_errors"]}
        elif args.command == "new-pcb-plan":
            result = {"plan_path": str(new_pcb_plan(args.snapshot, args.schematic, args.intent,
                      args.request, args.component_id, args.net))}
        elif args.command == "pcb-workflow":
            from .pcb import workflow as pcb_workflow_guide
            result = pcb_workflow_guide(args.intent)
        elif args.command == "compare-pcb":
            from .pcb import compare as compare_pcb
            result = compare_pcb(read_json(args.before), read_json(args.after))
        elif args.command == "review-pcb-plan":
            result = review_pcb(read_json(args.plan))
        elif args.command == "approve-pcb-plan":
            result = {"approval_path": str(approve_pcb(read_json(args.plan), args.shown_plan_sha256,
                      args.user_message, args.reference))}
        elif args.command == "execute-pcb-plan":
            pcb_plan_record = read_json(args.plan)
            pcb_preview = review_pcb(pcb_plan_record)
            if args.window_id and args.window_id != pcb_preview["target"]["window_id"]:
                raise ValueError("PCB执行窗口与方案基线不一致")
            result = execute_pcb(pcb_plan_record, args.approval,
                                 lambda: discover(args.port), experimental=args.experimental_pcb)
        elif args.command == "inspect-pcb":
            result = inspect_pcb(read_json(args.snapshot), net=args.net, component_id=args.component_id)
        elif args.command == "export-pcb":
            result = export_pcb_files(discover(args.port), args.window_id)
        elif args.command == "product-review":
            result = review_product(read_json(args.product))
        elif args.command == "drawing-trace":
            result = plan_trace(read_json(args.project), read_json(args.plan))
        elif args.command == "compare-design-audits":
            result = compare_files(args.before, args.after, args.save)
        elif args.command == "drawing-feedback":
            result = {"feedback_path": str(drawing_feedback(read_json(args.project), read_json(args.plan), args.comparison))}
        elif args.command == "prepare-source-validation":
            result = prepare_validation(args.source, args.audit, args.property, args.value)
        elif args.command == "check-source-validation":
            result = check_validation(args.case, args.source_after, args.audit_after, args.save)
        elif args.command == "check-source-roundtrip":
            expected = read_json(args.expected_drc_added) if args.expected_drc_added else []
            result = check_roundtrip(args.case, args.trial_source, args.trial_audit,
                                     args.restored_source, args.restored_audit, expected, args.save)
        elif args.command == "execute-source-trial":
            result = execute_trial(args.case, args.shown_sha256, args.user_message, args.reference,
                                   lambda: discover(args.port), args.reviewed_lock_sha256, backend=args.backend)
        elif args.command == "query":
            result = discover(args.port).design_query(args.kind, args.window_id, args.primitive_id)
            if args.save:
                result = {"query_path": str(write_new(initialize() / "queries" / (new_id("query") + ".json"), result))}
        elif args.command == "new-project":
            result = {"project_path": str(new_project(args.name, args.goal, interaction=args.interaction, scope=args.scope))}
        elif args.command == "validate-project":
            validate_project(read_json(args.project))
            result = {"valid": True, "scope": "仅检查结构、引用和任务依赖；不判定阶段通过或工程正确性"}
        elif args.command == "new-drawing-plan":
            result = {"plan_path": str(new_drawing_plan(read_json(args.project), args.request,
                      task_path=args.task, recommendation_ids=args.recommendation_id, decision_ids=args.decision_id))}
        elif args.command == "review-drawing-plan":
            result = review_drawing_plan(read_json(args.project), read_json(args.plan))
        elif args.command == "drawing-snapshot":
            result = {"snapshot_path": str(capture_snapshot(discover(args.port), args.window_id))}
        elif args.command == "capture-incremental-baseline":
            from .drawing_incremental import capture_baseline
            bundle = capture_baseline(discover(args.port), args.window_id)
            path = write_new(initialize() / "incremental-baselines" / (new_id("baseline") + ".json"), bundle)
            result = {"baseline_path": str(path), "baseline_sha256": digest(path)}
        elif args.command == "drawing-svg":
            record = discover(args.port).drawing_svg(args.window_id)
            path = write_new(initialize() / "drawing-snapshots" / (new_id("svg") + ".json"), record)
            svg_path = path.with_suffix(".svg")
            with svg_path.open("x", encoding="utf-8") as stream:
                stream.write(record["svg"])
            result = {"evidence_path": str(path), "svg_path": str(svg_path)}
        elif args.command == "review-drawing-visual":
            from .drawing_visual import review_files
            result = review_files(args.after, args.before, args.save)
        elif args.command == "audit-drawing-circuit":
            snapshot = read_json(args.snapshot)
            plan = read_json(args.plan)
            validate_snapshot(snapshot)
            if plan.get("execution_contract") != CONTRACT:
                raise ValueError("只支持 schematic-circuit-v3 审计计划")
            validate_circuit(plan, snapshot)
            raw = discover(args.port).drawing_circuit_audit(snapshot)
            summary = audit_circuit(raw, snapshot, {**plan, "result_designators": {}})
            path = write_new(initialize() / "drawing-audits" / (new_id("audit") + ".json"),
                             {"snapshot_path": str(args.snapshot.resolve()), "plan_path": str(args.plan.resolve()),
                              "raw": raw, "summary": summary})
            result = {"audit_path": str(path), "summary": summary}
        elif args.command == "drawing-components":
            record = discover(args.port).drawing_components(args.window_id)
            result = {"components_path": str(write_new(initialize() / "drawing-snapshots" /
                      (new_id("components") + ".json"), record)), "total": record["total"], "truncated": record["truncated"]}
        elif args.command == "drawing-device-search":
            record = discover(args.port).search_drawing_devices(args.keyword, args.window_id)
            result = {"search_path": str(write_new(initialize() / "drawing-snapshots" /
                      (new_id("device-search") + ".json"), record)), "count": len(record["items"])}
        elif args.command == "drawing-pins":
            record = discover(args.port).drawing_pins(args.primitive_id, args.window_id)
            result = {"pins_path": str(write_new(initialize() / "drawing-snapshots" /
                      (new_id("pins") + ".json"), record)), "total": record["total"], "truncated": record["truncated"]}
        elif args.command == "drawing-device-details":
            record = discover(args.port).drawing_device_details(args.library_uuid, args.device_uuid, args.window_id)
            result = {"details_path": str(write_new(initialize() / "drawing-snapshots" /
                      (new_id("device-details") + ".json"), record))}
        elif args.command == "drawing-project-info":
            record = discover(args.port).drawing_project_info(args.window_id)
            result = {"project_info_path": str(write_new(initialize() / "drawing-snapshots" /
                      (new_id("project-info") + ".json"), record))}
        elif args.command == "relocate-drawing-component":
            snapshot = read_json(args.snapshot)
            bridge = discover(args.port)
            if args.window_id and args.window_id != snapshot["window_id"]:
                raise ValueError("--window-id 必须与快照窗口一致")
            result = bridge.drawing_relocate_component(
                snapshot, args.primitive_id, args.project_device_uuid,
                {"x": args.from_x, "y": args.from_y, "designator": args.from_designator},
                {"x": args.to_x, "y": args.to_y, "designator": args.to_designator})
        elif args.command == "drawing-device-archive":
            record = discover(args.port).drawing_device_archive(args.library_uuid, args.device_uuid, args.window_id)
            result = {"archive_path": str(write_new(initialize() / "drawing-snapshots" /
                      (new_id("device-archive") + ".json"), record))}
        elif args.command == "prepare-drawing-execution":
            checked, _ = preflight(read_json(args.project), read_json(args.plan), for_execution=False)
            result = {**checked["preview"], **{k: checked[k] for k in ("route_metrics", "layout_review") if k in checked}}
        elif args.command == "approve-drawing-plan":
            result = {"approval_path": str(approve_plan(read_json(args.project), read_json(args.plan),
                      args.shown_plan_sha256, args.user_message, args.reference))}
        elif args.command == "execute-drawing-plan":
            result = execute_plan(read_json(args.project), read_json(args.plan), args.approval,
                                  lambda: discover(args.port))
        elif args.command == "project-guide":
            result = {"stage": args.stage, "guide": stage_guide(args.stage)}
        elif args.command == "project-review":
            result = project_review(read_json(args.project), args.project)
        elif args.command == "project-impact":
            result = project_impact(read_json(args.previous), read_json(args.current))
        elif args.command == "project-revise":
            result = {"project_path": str(revise_project(args.previous, args.current, args.reason))}
        elif args.command == "guide":
            result = {"mode": args.mode, "guide": guide(args.mode)}
        elif args.command == "status":
            bridge = discover(args.port)
            result = {"port": bridge.port, "health": bridge.health(), "windows": bridge.windows()}
        elif args.command == "connect":
            result = connect_api(args.timeout, args.skill_dir, args.window_id,
                                 progress=lambda message: print(message, file=sys.stderr, flush=True))
            result["status_path"] = str(write_new(
                external(initialize() / "connections" / (new_id("connection") + ".json")),
                {**result, "captured_at": now()},
            ))
        elif args.command == "context":
            result = discover(args.port).capture(args.window_id)
            if args.save:
                path = external(initialize() / "contexts" / (new_id("context") + ".json"))
                result = {"context_path": str(write_new(path, result))}
        elif args.command == "schematic":
            result = discover(args.port).schematic(args.window_id)
            if args.save:
                path = external(initialize() / "contexts" / (new_id("schematic") + ".json"))
                result = {"snapshot_path": str(write_new(path, result))}
        elif args.command == "inspect":
            result = neighborhood(read_json(args.snapshot), designator=args.designator,
                                  component_id=args.component_id, net=args.net,
                                  depth=args.depth, limit=args.limit)
            result['snapshot_path'] = str(args.snapshot.resolve())
        elif args.command == "datasheets":
            task_record = read_json(args.task)
            validate_task(task_record)
            result = {"datasheets": task_record.get("datasheets", []),
                      "hint": "report_path 指向手册访问清单；用 datasheet-search/page 按需查询"}
        elif args.command == "datasheet-search":
            result = search_document(args.manifest, args.query, args.limit)
        elif args.command == "datasheet-page":
            result = read_page(args.manifest, args.page, args.render)
        elif args.command == "new-task":
            if not args.live and (args.port is not None or args.window_id is not None):
                raise ValueError("--port/--window-id 需要 --live")
            context = read_json(args.context) if args.context else None
            if args.live:
                context = discover(args.port).capture(args.window_id)
            if context is not None:
                validate_context(context)
            result = {"task_path": str(create_task(args.mode, args.goal, args.constraint, context,
                      interaction=args.interaction, scope=args.scope, signals=args.signal, missing=args.missing))}
        elif args.command == "validate-task":
            validate_task(read_json(args.task))
            result = {"valid": True, "scope": "仅检查记录结构与引用关系，不验证工程正确性"}
        elif args.command == "handoff":
            result = {"manifest_path": str(handoff(args.task))}
        elif args.command == "import-datasheet":
            result = {"task_path": str(import_datasheet(args.task, args.file,
                                                         manufacturer=args.manufacturer, mpn=args.mpn,
                                                         primitive_id=args.primitive_id, revision=args.revision))}
    except (OSError, ValueError, KeyError, TypeError, BridgeError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "execute-drawing-plan":
        return 0 if result["status"] == "verified" else 3
    if args.command == "execute-pcb-plan":
        return 0 if result["status"] == "verified" else 3
    if args.command == "execute-source-trial":
        return 0 if result["status"] == "property_observed" else 3
    if args.command == "connect" and result["status"] != "connected":
        # timeout=0 explicitly asks only for a running bridge.
        return 0 if args.timeout == 0 and not args.window_id and result["status"] == "waiting_for_client" else 3
    return 0
