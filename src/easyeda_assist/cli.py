from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bridge import BridgeError, discover
from .connection import connect as connect_api
from .contracts import MODES, validate_context, validate_task
from .datasheets import import_datasheet, read_page, search_document
from .guidance import guide
from .schematic import neighborhood
from .storage import external, initialize, new_id, now, read_json, write_new
from .tasks import create_task, handoff


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EasyEDA 设计助手基础工具；当前只读、建议由 agent 完成")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化外部数据目录")
    sub.add_parser("modes", help="列出任务模式和所需输入")
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
    if args.command == "connect" and result["status"] != "connected":
        # timeout=0 explicitly asks only for a running bridge.
        return 0 if args.timeout == 0 and not args.window_id and result["status"] == "waiting_for_client" else 3
    return 0
