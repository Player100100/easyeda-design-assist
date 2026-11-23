"""Offline drawing-plan records. This module never executes CAD operations."""
from __future__ import annotations

from pathlib import Path

from .project import DRAWING_OPERATIONS, object_digest, text_value, validate_project
from .storage import external, initialize, new_id, now, write_new
from .drawing_api import SUPPORTED_OPERATIONS
from .drawing_readability import layout_review
from .operations import capabilities
from .traceability import make_origin, validate_origin


def new_drawing_plan(project: dict, request: str, *, task_path=None, recommendation_ids=(), decision_ids=()) -> Path:
    validate_project(project)
    if (project.get("drawing_policy") or {}).get("mode") == "off":
        raise ValueError("项目已关闭绘图计划，请先调整 drawing_policy")
    if not text_value(request):
        raise ValueError("绘图请求不能为空")
    origin = make_origin(project, task_path, recommendation_ids, decision_ids)
    plan = {"schema_version": 2, "interaction": "collaborative", "origin": origin,
            "project_id": project["id"], "created_at": now(),
            "request": request.strip(), "target": {"window_id": "", "project_uuid": "",
            "document_uuid": "", "domain": "", "snapshot_path": "", "snapshot_sha256": ""},
            "operations": [], "evidence": [], "unresolved": [], "readback_plan": "",
            "failure_plan": "", "approval": None}
    root = external(initialize() / "drawing-plans" / new_id("drawing-plan"))
    return write_new(root / "plan.json", plan)


def review_drawing_plan(project: dict, plan: dict) -> dict:
    validate_project(project)
    if not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] not in (1, 2):
        raise ValueError("绘图计划 schema_version 必须为 1 或 2")
    if plan["schema_version"] == 2:
        if plan.get("interaction") != "collaborative":
            raise ValueError("可执行绘图计划必须明确为 collaborative")
        validate_origin(project, plan.get("origin"), check_files=False)
    if plan.get("project_id") != project["id"]:
        raise ValueError("绘图计划与项目 ID 不一致")
    if not text_value(plan.get("request")) or not text_value(plan.get("created_at")):
        raise ValueError("绘图计划缺少请求或创建时间")
    target, operations = plan.get("target"), plan.get("operations")
    if not isinstance(target, dict) or not isinstance(operations, list) or any(not isinstance(op, dict) for op in operations):
        raise ValueError("绘图计划 target 必须是对象且 operations 必须是对象数组")
    findings = []
    for field in ("window_id", "project_uuid", "document_uuid", "snapshot_path", "snapshot_sha256"):
        if not text_value(target.get(field)):
            findings.append("目标缺少 " + field)
    sha = target.get("snapshot_sha256", "")
    if text_value(sha) and (len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
        findings.append("snapshot_sha256 格式无效")
    if not isinstance(target.get("domain"), str) or target["domain"] not in {"schematic", "pcb"}:
        findings.append("目标 domain 待定或无效")
    policy = project.get("drawing_policy") or {"mode": "draft_only", "allowed_operations": []}
    policy_findings = []
    if policy["mode"] == "off":
        policy_findings.append("项目已关闭绘图计划")
    elif policy["mode"] == "draft_only":
        policy_findings.append("项目仅允许准备方案")
    if not operations:
        findings.append("尚无具体操作")
    ids = set()
    for index, op in enumerate(operations):
        label = "operations/" + str(index)
        for field in ("id", "operation", "target", "before", "after", "expected_result"):
            if not text_value(op.get(field)):
                findings.append(label + " 缺少 " + field)
        op_id = op.get("id")
        if text_value(op_id):
            if op_id in ids:
                findings.append(label + " 的 id 重复")
            ids.add(op_id)
        kind = op.get("operation")
        if not isinstance(kind, str) or kind not in DRAWING_OPERATIONS:
            findings.append(label + " 的操作类型无效")
        if policy["mode"] == "explicit_batch" and kind not in policy["allowed_operations"]:
            policy_findings.append(label + " 不在项目允许操作中")
    for field in ("evidence", "unresolved"):
        value = plan.get(field)
        if not isinstance(value, list) or any(not text_value(x) for x in value):
            raise ValueError(field + " 必须是非空字符串组成的数组")
    if not plan["evidence"]:
        findings.append("缺少依据记录")
    if plan["unresolved"]:
        findings.append("存在未解决事项")
    for field in ("readback_plan", "failure_plan"):
        if not text_value(plan.get(field)):
            findings.append("缺少 " + field)
    # No approval data participates in the digest; reporting is never approval verification.
    plan_hash = object_digest({k: v for k, v in plan.items() if k != "approval"})
    report = {"project_id": project["id"], "policy_mode": policy["mode"],
            "plan_sha256": plan_hash, "findings": findings,
            "record_complete": not findings, "policy_findings": policy_findings,
            "within_policy_scope": not policy_findings, "execution_available": False,
            "approval_verified": False, "supported_execution_operations": SUPPORTED_OPERATIONS[:],
            "capabilities": capabilities(),
            "limitations": ["只检查记录格式和策略范围，未读取目标快照或核验工程正确性。",
                            "此离线检查不启用执行器；填写 approval 不能构成授权验证或触发执行。",
                            "agent 须核对真实用户认可记录，才能经已核验的 API 执行方案范围内写入。"]}
    if target.get("domain") == "schematic":
        report["layout_review"] = layout_review()
    return report
