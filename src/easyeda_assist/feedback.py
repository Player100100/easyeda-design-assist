"""Generate independent follow-up records from a plan's immutable execution.

No task acceptance, engineering PASS, consent or execution lock is modified.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from .design_audit import load_comparison, source_digest
from .drawing_api import validate_snapshot
from .project import affected_objects, review_digest
from .storage import data_root, external, initialize, new_id, now, read_json, write_new
from .traceability import canonical_digest, plan_trace


def drawing_feedback(project, plan, comparison_path: Path | None = None):
    trace = plan_trace(project, plan)
    result, comparison, comparison_ref = trace["result"], None, None
    run_dir = external(data_root() / "drawing-executions" / trace["plan_sha256"])
    if result is not None:
        claim = read_json(run_dir / "claim.json")
        if claim.get("plan_sha256") != trace["plan_sha256"] or claim.get("project_id") != project["id"]:
            raise ValueError("执行领取记录与反馈计划不一致")
    if comparison_path is not None:
        if result is None or result.get("status") != "verified":
            raise ValueError("审计比较只能绑定已完成回读的执行；未知状态先核对现场")
        comparison, comparison_ref = load_comparison(comparison_path)
        count = result.get("completed")
        if type(count) is not int or count != len(plan["operations"]) or count < 1:
            raise ValueError("执行记录没有完整的步骤范围")
        before = read_json(run_dir / "input-snapshot.json")
        after = read_json(run_dir / f"{count:03d}-snapshot.json")
        validate_snapshot(before)
        validate_snapshot(after)
        if (before["identity"] != after["identity"] or
                any(comparison["target"][key] != before["identity"][key] for key in comparison["target"]) or
                comparison["before"]["source_sha256"] != source_digest(before["source"]) or
                comparison["after"]["source_sha256"] != source_digest(after["source"])):
            raise ValueError("审计比较未绑定本次执行的前后快照")
    actions = []
    def action(kind, description):
        actions.append({"action": kind, "description": description})
    if trace["needs_review"]:
        action("revisit_origin", "原任务/决策依据已失效，复核相关判断并修订后续方案。")
    status = result.get("status") if result else None
    if status is None:
        action("inspect_execution" if trace["claimed"] else "prepare_plan",
               "方案已领取但缺少结果，先检查现场和日志。" if trace["claimed"] else "完善具体方案与前置证据；本反馈不构成认可。")
    elif status in {"uncertain", "partial", "verification_failed"}:
        action("inspect_scene_and_request", "检查已完成项、真实画布与原请求状态；保留锁，不自动重试或修补。")
        action("review_affected_design", "核对实际影响，必要时重新采集快照并形成修订方案。")
    elif status == "blocked":
        action("resolve_preconditions", "核对阻断原因与目标前提；原领取记录保留，后续另建方案。")
    elif status == "verified":
        if comparison is None:
            action("capture_audit_comparison", "取得与执行前后源绑定的网表/详细DRC证据，检查受影响连接。")
        else:
            if not comparison["no_observed_regressions"]:
                action("review_audit_differences", "审计有连接变化、DRC变化、未匹配项或版本不兼容，逐项核查。")
            if not comparison["freshness_established"]:
                action("establish_derived_freshness", "补充网表/DRC确已反映本次Source的证据；稳定旧结果不能关闭核验。")
        action("engineering_validation", "继续原建议的计算、工况与实板验证；CAD回读通过不关闭工程验收。")
    else:
        raise ValueError("执行结果状态不受支持，不能生成完成反馈")
    origin = plan.get("origin") or {"task": None, "decisions": []}
    seeds = {"decisions/" + item["id"] for item in origin["decisions"]
             if any(d["id"] == item["id"] for d in project["decisions"])}
    if origin["task"] is not None:
        seeds.update("tasks/" + item["id"] for item in project["tasks"]
                     if item.get("task_id") == origin["task"]["task_id"])
    needs_review = (trace["needs_review"] or status != "verified" or comparison is None or
                    not comparison["no_observed_regressions"] or not comparison["freshness_established"])
    targets = [{"object": key, "reviewed_against": review_digest(project, key), "review_required": needs_review}
               for key in affected_objects(project, seeds)]
    validation_items = []
    evidence_path = run_dir / "origin-task-evidence.json"
    if result is not None and origin["task"] and evidence_path.is_file():
        evidence = read_json(evidence_path)
        raw = base64.b64decode(evidence["raw_base64"], validate=True)
        import hashlib
        if evidence.get("reference") != origin["task"] or hashlib.sha256(raw).hexdigest() != origin["task"]["sha256"]:
            raise ValueError("原任务归档证据与来源不一致")
        task = json.loads(raw.decode("utf-8-sig"))
        ids = {item["id"] for item in origin["task"]["recommendations"]}
        validation_items = [{"recommendation_id": item["id"], "validation": item["validation"]}
                            for item in task["recommendations"] if item["id"] in ids]
    record = {"schema_version": 1, "kind": "drawing-feedback", "created_at": now(),
              "project_id": project["id"], "plan_sha256": trace["plan_sha256"], "origin": origin,
              "execution_status": status, "execution": None if result is None else {
                  "path": trace["result_path"], "content_sha256": canonical_digest(result)},
              "source_findings": trace["findings"], "comparison_reference": comparison_ref,
              "comparison": comparison, "needs_review": needs_review, "review_targets": targets,
              "next_actions": actions, "original_validation_items": validation_items,
              "engineering_verified": False,
              "limitations": ["只生成独立反馈，不修改原任务/决策、项目完成状态、认可或执行锁。",
                              "复核对象仅沿显式项目引用传播，未登记的工程关联仍需人工核对。"]}
    return write_new(initialize() / "drawing-feedback" / trace["plan_sha256"] / (new_id("feedback") + ".json"), record)
