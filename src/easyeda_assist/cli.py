from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bridge import BridgeError, discover
from .contracts import MODES, validate_context, validate_task
from .storage import external, initialize, new_id, read_json, write_new
from .tasks import create_task, handoff


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EasyEDA 设计助手基础工具；当前只读、建议由 agent 完成")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化外部数据目录")
    sub.add_parser("modes", help="列出任务模式和所需输入")
    for name in ("status", "context"):
        command = sub.add_parser(name)
        command.add_argument("--port", type=int, help="本机 bridge 端口；默认扫描 49620–49629")
        if name == "context":
            command.add_argument("--window-id", help="多个窗口时必须明确指定")
            command.add_argument("--save", action="store_true", help="归档上下文并输出路径")
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
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = {"data_root": str(initialize())}
        elif args.command == "modes":
            result = {k: {"name": v[0], "required_inputs": v[1]} for k, v in MODES.items()}
        elif args.command == "status":
            bridge = discover(args.port)
            result = {"port": bridge.port, "health": bridge.health(), "windows": bridge.windows()}
        elif args.command == "context":
            result = discover(args.port).capture(args.window_id)
            if args.save:
                path = external(initialize() / "contexts" / (new_id("context") + ".json"))
                result = {"context_path": str(write_new(path, result))}
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
        else:
            result = {"manifest_path": str(handoff(args.task))}
    except (OSError, ValueError, KeyError, TypeError, BridgeError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
