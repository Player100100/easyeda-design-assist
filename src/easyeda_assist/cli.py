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
from .guidance import guide
from .project import (PROJECT_STATES, new_project, validate_project, stage_guide,
                      project_review, project_impact, revise_project)
from .schematic import neighborhood
from .storage import external, initialize, new_id, now, read_json, write_new
from .tasks import create_task, handoff


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EasyEDA 设计助手；固定查询及方案认可后的受限绘图")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化外部数据目录")
    sub.add_parser("modes", help="列出任务模式和所需输入")
    project = sub.add_parser("new-project", help="创建离线项目设计档案初稿")
    project.add_argument("--name", required=True)
    project.add_argument("--goal", required=True)
    check_project = sub.add_parser("validate-project", help="检查项目档案结构、引用和任务依赖")
    check_project.add_argument("project", type=Path)
    drawing = sub.add_parser("new-drawing-plan", help="离线创建绘图计划初稿，不执行 CAD 操作")
    drawing.add_argument("project", type=Path)
    drawing.add_argument("--request", required=True)
    drawing_review = sub.add_parser("review-drawing-plan", help="检查计划记录缺项与项目策略")
    drawing_review.add_argument("project", type=Path)
    drawing_review.add_argument("plan", type=Path)
    drawing_source = sub.add_parser("drawing-snapshot", help="只读归档当前原理图完整文档源，供绘图方案绑定")
    drawing_source.add_argument("--port", type=int)
    drawing_source.add_argument("--window-id")
    drawing_svg = sub.add_parser("drawing-svg", help="只读导出当前原理图原生SVG供视觉审核")
    drawing_svg.add_argument("--port", type=int)
    drawing_svg.add_argument("--window-id")
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
        elif args.command == "new-project":
            result = {"project_path": str(new_project(args.name, args.goal))}
        elif args.command == "validate-project":
            validate_project(read_json(args.project))
            result = {"valid": True, "scope": "仅检查结构、引用和任务依赖；不判定阶段通过或工程正确性"}
        elif args.command == "new-drawing-plan":
            result = {"plan_path": str(new_drawing_plan(read_json(args.project), args.request))}
        elif args.command == "review-drawing-plan":
            result = review_drawing_plan(read_json(args.project), read_json(args.plan))
        elif args.command == "drawing-snapshot":
            result = {"snapshot_path": str(capture_snapshot(discover(args.port), args.window_id))}
        elif args.command == "drawing-svg":
            record = discover(args.port).drawing_svg(args.window_id)
            path = write_new(initialize() / "drawing-snapshots" / (new_id("svg") + ".json"), record)
            svg_path = path.with_suffix(".svg")
            with svg_path.open("x", encoding="utf-8") as stream:
                stream.write(record["svg"])
            result = {"evidence_path": str(path), "svg_path": str(svg_path)}
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
            checked, _ = preflight(read_json(args.project), read_json(args.plan))
            result = checked["preview"]
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
            result = {"task_path": str(create_task(args.mode, args.goal, args.constraint, context))}
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
    if args.command == "connect" and result["status"] != "connected":
        # timeout=0 explicitly asks only for a running bridge.
        return 0 if args.timeout == 0 and not args.window_id and result["status"] == "waiting_for_client" else 3
    return 0
