"""Approval, journaling and fail-closed execution for the PCB contract."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from .pcb import (CONTRACT, preview, require, validate_operation, verify_operation,
                  output, source_records, same_source)
from .storage import digest, external, initialize, new_id, now, read_json, write_new


def _durable(path: Path, value: dict) -> Path:
    path = external(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def export_files(bridge, window_id=None):
    record = bridge.pcb_export(window_id)
    require(isinstance(record.get("identity"), dict) and isinstance(record.get("source"), str), "制造导出缺少PCB身份/完整源")
    expected = {"gerber": ".zip", "bom": ".csv", "pick_and_place": ".csv", "ipc_d356": ".net"}
    require(isinstance(record.get("files"), dict) and set(record["files"]) == set(expected), "PCB制造文件导出缺项")
    binary_files = {}
    for key, item in record["files"].items():
        require(isinstance(item, dict) and isinstance(item.get("base64"), str), "制造文件编码不兼容")
        value = base64.b64decode(item["base64"], validate=True)
        require(0 < len(value) <= 30*1024*1024 and item.get("size") == len(value), "制造文件字节长度不一致")
        binary_files[key] = value
    run = initialize() / "pcb-exports" / new_id("pcb-export")
    run.mkdir(parents=True, exist_ok=False)
    write_new(run / "raw.json", record)
    if record.get('stable') is False or ('source_after' in record and not same_source(record['source'],record['source_after'])):
        raise ValueError('制造导出期间设计变化，原始文件已隔离留档：'+str(run/'raw.json'))
    files = {}
    for key, value in binary_files.items():
        # Remote file names are metadata only, never filesystem paths.
        path = run / (key + expected[key])
        with path.open("xb") as stream:
            stream.write(value)
        files[key] = {"path": str(path), "sha256": digest(path), "size": len(value),
                      "original_name": record["files"][key].get("name")}
    manifest = {"schema_version": 1, "kind": "pcb-manufacturing-export", "captured_at": now(),
                "identity": record["identity"], "window_id": record.get("window_id"),
                "source_sha256": hashlib.sha256(record["source"].encode()).hexdigest(), "files": files,
                "limitations": ["文件已导出，尚需独立查看器及板厂确认；不表示已投板或工程性能验收。"]}
    path = write_new(run / "manifest.json", manifest)
    return {"manifest_path": str(path), **manifest}


def _bound(path_value: str, sha256: str):
    path = external(Path(path_value))
    if digest(path) != sha256:
        raise ValueError("PCB方案输入文件已变化")
    return path


def review(plan: dict) -> dict:
    checked, _ = preview(plan)
    return checked


def approve(plan: dict, shown_sha256: str, user_message: str, reference: str) -> Path:
    checked = review(plan)
    if not checked["ready_for_approval"]:
        raise ValueError("PCB方案尚未满足执行前检查：" + "；".join(checked["findings"]))
    if shown_sha256 != checked["plan_sha256"]:
        raise ValueError("展示摘要与当前PCB方案不一致")
    if not isinstance(user_message, str) or not user_message.strip() or not isinstance(reference, str) or not reference.strip():
        raise ValueError("需要记录用户认可原话和对话引用")
    return _durable(initialize() / "pcb-approvals" / (new_id("pcb-approval") + ".json"), {
        "schema_version": 1, "kind": "pcb-consent", "created_at": now(),
        "contract": CONTRACT, "plan_sha256": checked["plan_sha256"],
        "target": checked["target"], "scope": checked["scope"],
        "user_message": user_message.strip(), "reference": reference.strip(),
        "preview": checked, "limitation": "认可记录来自调用方对真实对话的留档；CLI不验证发言者身份。",
    })


def _verify_approval(checked, approval):
    if not isinstance(approval, dict) or approval.get("kind") != "pcb-consent" or approval.get("schema_version") != 1:
        raise ValueError("缺少PCB方案认可记录")
    if approval.get("contract") != CONTRACT or approval.get("plan_sha256") != checked["plan_sha256"]:
        raise ValueError("PCB认可记录已失效")
    if approval.get("preview") != checked:
        raise ValueError("PCB认可记录绑定的预览已经变化")
    require(all(isinstance(approval.get(k), str) and approval[k].strip() for k in ("user_message", "reference")), "PCB认可记录缺少真实原话/引用")


def execute(plan: dict, approval_path: Path, bridge_factory, *, experimental: bool = False) -> dict:
    checked = review(plan)
    require(checked["ready_for_approval"], "PCB方案不满足执行前检查")
    if not experimental:
        raise ValueError("PCB写入适配器仍属实验能力；实机测试板验证前请显式使用 --experimental-pcb")
    _verify_approval(checked, read_json(approval_path))
    baseline_path = _bound(plan["baseline"]["path"], plan["baseline"]["sha256"])
    before = read_json(baseline_path)
    from .pcb import sha
    root = initialize()
    run = root / "pcb-executions" / checked["plan_sha256"]
    run.mkdir(parents=True, exist_ok=False)
    identity = checked["target"]
    lock = root / "drawing-locks" / sha({"project_uuid": identity["project_uuid"], "document_uuid": identity["document_uuid"]})
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.mkdir(exist_ok=False)
    _durable(lock / "owner.json", {"contract": CONTRACT, "run_dir": str(run), "created_at": now()})
    _durable(run / "plan.json", plan)
    _durable(run / "approval.json", read_json(approval_path))
    completed = 0
    results = []
    try:
        bridge = bridge_factory()
        current = bridge.pcb_capture(before["window_id"], with_drc=True)
        require(same_source(current["source"],before["source"])
                and current["identity"] == before["identity"], "实时PCB与方案基线不一致；未执行写入")
        require(current.get("complete") is True and not current.get("read_errors"), "实时PCB读取不完整")
        for name in ("layers", "footprints", "stackup", "origin", "rules", "net_rules", "net_classes", "differential_pairs", "equal_length_groups", "nets", "drc"):
            require(current['outputs'].get(name) == before['outputs'].get(name), "实时PCB约束/DRC与批准基线不同：" + name)
        source_records(current["source"])
        _durable(run / "before.json", current)
        last = current
        for index, op in enumerate(plan["operations"], 1):
            validate_operation(op)
            _durable(run / f"{index:03d}-intent.json", {"operation": op, "captured_at": now()})
            result = bridge.pcb_operation(last, op)
            _durable(run / f"{index:03d}-reply.json", result)
            if result.get("status") != "observed" or not isinstance(result.get("after"), dict):
                raise ValueError("PCB操作未完成独立回读：" + str(result.get("error", result.get("status"))))
            engine = result.get("engine_result")
            if op["operation"] in {"auto_place", "route_nets"}:
                require(isinstance(engine, dict) and engine.get("success") is True, "原生引擎报告未完成；保留候选结果后重新规划")
            after = result["after"]
            verification = verify_operation(last, after, op, result.get("created_id"), plan["scope"], plan["constraints"])
            _durable(run / f"{index:03d}-verification.json", verification)
            results.append(verification)
            last = after
            completed += 1
        _durable(run / "after.json", last)
        _durable(run / "result.json", {"status": "verified", "completed": completed,
                                        "captured_at": now(), "results": results})
        (lock / "owner.json").unlink()
        lock.rmdir()
        return {"status": "verified", "completed": completed, "run_dir": str(run), "results": results,
                "limitation": "仅证明声明操作的PCB源回读与详细DRC未新增；不证明电气、热、SI/PI或实板性能"}
    except Exception as exc:
        _durable(run / "failure.json", {"status": "uncertain", "completed": completed,
                                         "error": str(exc), "captured_at": now()})
        return {"status": "uncertain", "completed": completed, "run_dir": str(run),
                "target_lock": str(lock), "error": str(exc), "limitation": "写入可能已发生，保留现场且不自动重试/回滚"}
