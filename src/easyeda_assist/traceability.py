"""Immutable links between engineering advice, a plan and its execution evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import validate_task
from .storage import data_root, external, read_json


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def task_reference(path, recommendation_ids):
    path = external(Path(path))
    raw = path.read_bytes()
    task = json.loads(raw.decode("utf-8-sig"))
    validate_task(task)
    if not recommendation_ids or len(recommendation_ids) != len(set(recommendation_ids)):
        raise ValueError("必须明确选择不重复的 recommendation ID")
    suggestions = {r["id"]: r for r in task["recommendations"]}
    if any(rid not in suggestions for rid in recommendation_ids):
        raise ValueError("任务中不存在指定建议")
    if task["missing_inputs"] or any(suggestions[rid]["status"] == "needs_input" for rid in recommendation_ids):
        raise ValueError("当前建议仍有关键缺项，先补依据再生成操作方案")
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "task_id": task["id"],
            "recommendations": [{"id": rid, "sha256": canonical_digest(suggestions[rid])} for rid in recommendation_ids]}


def validate_origin(project, origin, check_files=True):
    if not isinstance(origin, dict) or set(origin) != {"task", "decisions"}:
        raise ValueError("origin 必须包含 task 和 decisions")
    decisions = origin["decisions"]
    if not isinstance(decisions, list) or any(not isinstance(item, dict) or set(item) != {"id", "sha256"} for item in decisions):
        raise ValueError("origin.decisions 格式无效")
    if any(not isinstance(item["id"], str) for item in decisions) or len({x["id"] for x in decisions}) != len(decisions):
        raise ValueError("决策引用 ID 无效或重复")
    available = {item["id"]: item for item in project["decisions"]}
    for item in decisions:
        sha = item["sha256"]
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("决策引用摘要无效")
        if check_files and (item["id"] not in available or canonical_digest(available[item["id"]]) != sha):
            raise ValueError("原工程决策已变化或不存在，须复核方案")
    ref = origin["task"]
    if ref is None:
        return
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "task_id", "recommendations"}:
        raise ValueError("origin.task 格式无效")
    if not isinstance(ref["path"], str) or not Path(ref["path"]).is_absolute():
        raise ValueError("任务引用必须是外部绝对路径")
    if not isinstance(ref["task_id"], str) or not ref["task_id"].strip():
        raise ValueError("任务引用缺少 task_id")
    refs = ref["recommendations"]
    if not isinstance(refs, list) or not refs or any(
        not isinstance(x, dict) or set(x) != {"id", "sha256"} or not isinstance(x["id"], str) for x in refs
    ):
        raise ValueError("建议引用格式无效")
    for sha in [ref["sha256"], *(x["sha256"] for x in refs), *(x["sha256"] for x in decisions)]:
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("origin 引用摘要无效")
    if check_files and task_reference(ref["path"], [x["id"] for x in refs]) != ref:
        raise ValueError("原任务或建议依据已变化，须复核方案")


def make_origin(project, task_path=None, recommendation_ids=(), decision_ids=()):
    if task_path is None and recommendation_ids:
        raise ValueError("指定建议 ID 需要 task_path")
    if len(decision_ids) != len(set(decision_ids)):
        raise ValueError("决策引用不得重复")
    available = {item["id"]: item for item in project["decisions"]}
    if any(did not in available for did in decision_ids):
        raise ValueError("项目中不存在指定决策")
    return {"task": task_reference(task_path, recommendation_ids) if task_path is not None else None,
            "decisions": [{"id": did, "sha256": canonical_digest(available[did])} for did in decision_ids]}


def execution_project_digest(project):
    """Version 2 excludes progress metadata; all unknown/design fields stay bound."""
    ignored = {"created_at", "updated_at", "status", "change_log", "review_records", "pending_reviews"}
    value = {k: v for k, v in project.items() if k not in ignored}
    value["tasks"] = [{k: v for k, v in task.items() if k != "status"} for task in project["tasks"]]
    return canonical_digest(value)


def plan_trace(project, plan):
    from .drawing import review_drawing_plan
    review = review_drawing_plan(project, plan)
    problems = []
    try:
        validate_origin(project, plan.get("origin", {"task": None, "decisions": []}))
    except (OSError, ValueError) as exc:
        problems.append(str(exc))
    run_dir = external(data_root() / "drawing-executions" / review["plan_sha256"])
    result_path = run_dir / "result.json"
    result = read_json(result_path) if result_path.is_file() else None
    if result is not None and (result.get("plan_sha256", review["plan_sha256"]) != review["plan_sha256"] or
                               result.get("project_id", project["id"]) != project["id"]):
        raise ValueError("执行结果与当前计划身份不一致")
    return {"project_id": project["id"], "plan_sha256": review["plan_sha256"], "origin": plan.get("origin"),
            "needs_review": bool(problems), "findings": problems,
            "claimed": (run_dir / "claim.json").is_file(),
            "result_path": str(result_path) if result is not None else None, "result": result,
            "feedback_paths": [str(path) for path in sorted(
                external(data_root() / "drawing-feedback" / review["plan_sha256"]).glob("feedback-*.json"))],
            "engineering_validation": "not-established-by-cad-execution"}
