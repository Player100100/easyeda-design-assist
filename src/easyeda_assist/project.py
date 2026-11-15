"""Lightweight, traceable project design records for board-level work."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .contracts import validate_task
from .storage import PROJECT_ROOT, digest, external, initialize, new_id, now, read_json, write_new

PROJECT_STATES = {"framing", "architecture", "design", "integration", "verification", "released"}
PRIORITIES = {"must", "should", "could"}
VERIFICATION_METHODS = {"analysis", "inspection", "demonstration", "test"}
TASK_STATES = {"blocked", "ready", "in_progress", "done", "deferred"}
DRAWING_POLICY_MODES = {"off", "draft_only", "explicit_batch"}
DRAWING_OPERATIONS = {"place_component", "place_net_flag", "move_component", "rotate_component",
                      "add_wire", "edit_wire", "set_property", "place_graphic"}
SECTIONS = ("requirements", "modules", "interfaces", "budgets", "decisions", "tasks",
            "verifications", "sources", "risks")
REFERENCE_FIELDS = {"requirement_ids": "requirements", "module_ids": "modules",
                    "interface_ids": "interfaces", "budget_ids": "budgets",
                    "decision_ids": "decisions", "task_ids": "tasks",
                    "verification_ids": "verifications", "source_ids": "sources", "risk_ids": "risks"}


def text_value(value):
    return isinstance(value, str) and bool(value.strip())


def string_list(value):
    return isinstance(value, list) and all(text_value(item) for item in value)


def _items(value, field):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} 必须是对象数组")
    ids = [item.get("id") for item in value]
    if any(not isinstance(item_id, str) or not item_id.strip() for item_id in ids):
        raise ValueError(f"{field} 每项必须有非空 id")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{field} id 不得重复")
    return {item_id for item_id in ids}


def validate_project(project):
    if not isinstance(project, dict) or type(project.get("schema_version")) is not int or project.get("schema_version") != 1:
        raise ValueError("项目 schema_version 必须为 1")
    for key in ("id", "name", "goal", "created_at", "updated_at"):
        if not isinstance(project.get(key), str) or not project[key].strip():
            raise ValueError(f"项目缺少 {key}")
    if project.get("status") not in PROJECT_STATES:
        raise ValueError("项目状态无效")
    if not isinstance(project.get("scope"), list) or not all(isinstance(x, str) and x.strip() for x in project["scope"]):
        raise ValueError("scope 必须是字符串数组")
    policy = project.get("drawing_policy")
    if policy is not None:
        if not isinstance(policy, dict) or not isinstance(policy.get("mode"), str) or policy["mode"] not in DRAWING_POLICY_MODES:
            raise ValueError("drawing_policy.mode 无效")
        if policy.get("trigger") != "explicit_user_request":
            raise ValueError("绘图触发条件必须是 explicit_user_request")
        if type(policy.get("require_batch_approval")) is not bool or not policy["require_batch_approval"]:
            raise ValueError("每个绘图批次都必须要求用户明确授权")
        if type(policy.get("require_snapshot_binding")) is not bool or not policy["require_snapshot_binding"]:
            raise ValueError("绘图批次必须绑定目标文档和执行前快照")
        operations = policy.get("allowed_operations")
        if not isinstance(operations, list) or any(not isinstance(op, str) or op not in DRAWING_OPERATIONS for op in operations):
            raise ValueError("drawing_policy.allowed_operations 含无效操作")
        if len(operations) != len(set(operations)):
            raise ValueError("drawing_policy.allowed_operations 不得重复")
        if policy["mode"] != "explicit_batch" and operations:
            raise ValueError("仅 explicit_batch 模式可以配置允许操作")

    requirements = project.get("requirements")
    req_ids = _items(requirements, "requirements")
    for req in requirements:
        for key in ("statement", "source", "verification_method", "status"):
            if not isinstance(req.get(key), str) or not req[key].strip():
                raise ValueError(f"需求 {req['id']} 缺少 {key}")
        if req.get("priority") not in PRIORITIES:
            raise ValueError(f"需求 {req['id']} priority 无效")
        if req["verification_method"] not in VERIFICATION_METHODS | {"tbd"}:
            raise ValueError(f"需求 {req['id']} verification_method 无效")
        if req["status"] not in {"open", "allocated", "verified", "accepted", "deferred"}:
            raise ValueError(f"需求 {req['id']} status 无效")

    modules = _items(project.get("modules"), "modules")
    _items(project.get("interfaces"), "interfaces")
    _items(project.get("budgets"), "budgets")
    _items(project.get("decisions"), "decisions")
    tasks = project.get("tasks")
    task_ids = _items(tasks, "tasks")
    verifications = project.get("verifications")
    _items(verifications, "verifications")
    groups = {section: _items(project.get(section, []), section) for section in SECTIONS}
    for field in ("assumptions", "open_questions", "limitations", "pending_reviews"):
        if field in project and not string_list(project[field]):
            raise ValueError(f"{field} 必须是字符串数组")

    def refs(item, key, allowed, label):
        value = item.get(key, [])
        if not isinstance(value, list) or any(not isinstance(ref, str) or ref not in allowed for ref in value):
            raise ValueError(f"{label} {item['id']} 引用了不存在的 {key}")
        if len(value) != len(set(value)):
            raise ValueError(f"{label} {item['id']} 的 {key} 存在重复引用")

    for section in SECTIONS:
        for item in project.get(section, []):
            for field, target in REFERENCE_FIELDS.items():
                refs(item, field, groups[target], section)
            for field in ("task_path", "task_id", "task_sha256"):
                if field in item and not text_value(item[field]):
                    raise ValueError(f"{section} {item['id']} 的 {field} 必须非空")
            if "task_sha256" in item:
                value = item["task_sha256"]
                if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                    raise ValueError("task_sha256 必须为小写 SHA256")

    for interface in project["interfaces"]:
        refs(interface, "modules", modules, "接口")
        if len(interface.get("modules", [])) != 2:
            raise ValueError(f"接口 {interface['id']} 必须连接两个模块")
    for budget in project["budgets"]:
        refs(budget, "modules", modules, "预算")
    for decision in project["decisions"]:
        refs(decision, "requirement_ids", req_ids, "决策")
        refs(decision, "module_ids", modules, "决策")
        if decision.get("status") not in {"open", "selected", "rejected", "revisit"}:
            raise ValueError(f"决策 {decision['id']} status 无效")
    for task in tasks:
        if task.get("status") not in TASK_STATES:
            raise ValueError(f"任务 {task['id']} status 无效")
        refs(task, "requirement_ids", req_ids, "任务")
        refs(task, "module_ids", modules, "任务")
        refs(task, "depends_on", task_ids, "任务")
        linked_fields = {"task_path", "task_id", "task_sha256"} & task.keys()
        if linked_fields and linked_fields != {"task_path", "task_id", "task_sha256"}:
            raise ValueError(f"任务 {task['id']} 关联档案需同时提供 task_path、task_id、task_sha256")
    remaining = {task["id"]: len(task.get("depends_on", [])) for task in tasks}
    children = {key: [] for key in remaining}
    for task in tasks:
        for parent in task.get("depends_on", []):
            children[parent].append(task["id"])
    queue = deque(key for key, count in remaining.items() if count == 0)
    visited_count = 0
    while queue:
        parent = queue.popleft()
        visited_count += 1
        for child in children[parent]:
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    if visited_count != len(tasks):
        raise ValueError("任务 depends_on 存在循环依赖")
    for verification in verifications:
        refs(verification, "requirement_ids", req_ids, "验证")
        if verification.get("method") not in VERIFICATION_METHODS | {"tbd"}:
            raise ValueError(f"验证 {verification['id']} method 无效")
        if verification.get("result") not in {"planned", "pass", "fail", "blocked"}:
            raise ValueError(f"验证 {verification['id']} result 无效")
    for source in project.get("sources", []):
        for field in ("reference", "locator", "description"):
            if not text_value(source.get(field)):
                raise ValueError(f"来源 {source['id']} 缺少 {field}")
    for risk in project.get("risks", []):
        if not text_value(risk.get("statement")) or risk.get("status") not in {"open", "mitigating", "closed", "accepted"}:
            raise ValueError(f"风险 {risk['id']} 缺少 statement 或 status 无效")
    for field in ("change_log", "review_records"):
        if field in project and (not isinstance(project[field], list) or any(not isinstance(x, dict) for x in project[field])):
            raise ValueError(f"{field} 必须是对象数组")
    for review in project.get("review_records", []):
        if any(not text_value(review.get(key)) for key in ("object", "record", "reviewed_against")):
            raise ValueError("复核记录需 object、record 和 reviewed_against")


def new_project(name: str, goal: str):
    if not isinstance(name, str) or not name.strip() or not isinstance(goal, str) or not goal.strip():
        raise ValueError("项目名称和目标不能为空")
    timestamp = now()
    project = {
        "schema_version": 1, "id": new_id("project"), "name": name.strip(),
        "goal": goal.strip(), "status": "framing", "created_at": timestamp,
        "updated_at": timestamp, "scope": [], "assumptions": [], "open_questions": [],
        "requirements": [], "modules": [], "interfaces": [], "budgets": [],
        "decisions": [], "tasks": [], "verifications": [], "sources": [],
        "risks": [], "change_log": [], "review_records": [], "pending_reviews": [],
        "drawing_policy": {"mode": "explicit_batch", "trigger": "explicit_user_request",
                           "allowed_operations": sorted(DRAWING_OPERATIONS), "require_batch_approval": True,
                           "require_snapshot_binding": True},
        "limitations": ["本档案组织设计输入和验证追踪；结构校验不证明工程正确性。",
                        "状态变化和已接受的决定应保留记录；变更需复核受影响接口、任务和验证项。"]
    }
    validate_project(project)
    root = external(initialize() / "projects" / project["id"])
    root.mkdir(parents=True, exist_ok=False)
    path = write_new(root / "project.json", project)
    (root / "README.md").write_text(
        f"# {name.strip()}\n\n目标：{goal.strip()}\n\n"
        "从 scope、open_questions 和 requirements 开始。需求写用户可观察、可验证的结果，"
        "不要把器件型号或实现方式伪装成需求。随后建立模块、模块接口、预算、方案决策和依赖任务，"
        "为每条需求安排分析、检查、演示或实测。未知信息保留为 TBD；不得把结构完整当作设计通过。\n\n"
        "字段说明见 `docs/project-design.md`；阶段方法见 `docs/project-stages.md`。"
        "记录检查：`python -B src/design_assist.py project-review <project.json>`。\n",
        encoding="utf-8")
    return path


def stage_guide(stage: str) -> str:
    if stage not in PROJECT_STATES:
        raise ValueError("项目阶段无效")
    content = (PROJECT_ROOT / "docs" / "project-stages.md").read_text(encoding="utf-8")
    marker = f"## {stage}\n"
    if marker not in content:
        raise ValueError(f"缺少阶段指引：{stage}")
    section = content.split(marker, 1)[1].split("\n## ", 1)[0]
    return section.strip()


def _object_map(project):
    return {f"{section}/{item['id']}": item for section in SECTIONS for item in project.get(section, [])}


def object_digest(item):
    return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def review_digest(project, key):
    # Bind reviews to the design snapshot, not just the unchanged consumer.
    # Otherwise an old review would clear a new upstream change automatically.
    return object_digest({"object": key, "design": {
        field: project.get(field, [] if field in SECTIONS else None)
        for field in (*SECTIONS, "name", "goal", "scope", "assumptions", "open_questions", "drawing_policy")}})


def pending_reviews(project):
    pending = []
    for key in project.get("pending_reviews", []):
        target_digest = review_digest(project, key)
        if not any(review["object"] == key and review["reviewed_against"] == target_digest
                   for review in project.get("review_records", [])):
            pending.append(key)
    return pending


def project_impact(previous, current):
    """Conservative reachability, not an electrical or semantic change solver."""
    validate_project(previous)
    validate_project(current)
    if previous["id"] != current["id"]:
        raise ValueError("只能比较同一项目 id 的两个版本")
    before, after = _object_map(previous), _object_map(current)
    keys = set(before) | set(after)
    changed = {key for key in keys if before.get(key) != after.get(key)}
    global_fields = [key for key in ("name", "goal", "scope", "assumptions", "open_questions", "drawing_policy")
                     if previous.get(key) != current.get(key)]
    dependents = {key: set() for key in keys}
    # Both graphs matter: a removed reference must still invalidate its consumer.
    for objects in (before, after):
        for consumer, item in objects.items():
            section = consumer.split("/", 1)[0]
            fields = dict(REFERENCE_FIELDS)
            if section in {"interfaces", "budgets"}:
                fields["modules"] = "modules"
            if section == "tasks":
                fields["depends_on"] = "tasks"
            for field, target in fields.items():
                for ref in item.get(field, []):
                    dependents[f"{target}/{ref}"].add(consumer)
    affected = set(keys) if global_fields else set(changed)
    queue = deque(affected)
    while queue:
        for consumer in dependents[queue.popleft()]:
            if consumer not in affected:
                affected.add(consumer)
                queue.append(consumer)
    return {"project_id": current["id"], "global_changes": global_fields,
            "added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(changed), "affected": sorted(affected),
            "needs_review": sorted(affected),
            "limitations": ["仅沿显式引用追踪；未登记的电气、几何、固件或工况依赖不会被自动发现。",
                            "变更不自动撤销或重签既有测试结论；受影响对象须复核。"]}


def revise_project(previous_path: Path, current_path: Path, reason: str) -> Path:
    if not text_value(reason):
        raise ValueError("项目修订必须说明原因")
    # Read each version once: hashes refer to the exact bytes copied below.
    previous_bytes, current_bytes = previous_path.read_bytes(), current_path.read_bytes()
    previous = json.loads(previous_bytes.decode("utf-8-sig"))
    current = json.loads(current_bytes.decode("utf-8-sig"))
    impact = project_impact(previous, current)
    revision = deepcopy(current)
    revision["updated_at"] = now()
    # The new archive has a different directory; keep relative task links valid.
    for task in revision["tasks"]:
        if "task_path" in task and not Path(task["task_path"]).is_absolute():
            task["task_path"] = str((current_path.resolve().parent / task["task_path"]).resolve())
    # Carry forward outstanding reviews even if the edited copy omitted them.
    revision["pending_reviews"] = sorted(set(pending_reviews(previous)) |
                                         set(pending_reviews(current)) | set(impact["needs_review"]))
    # Imported project IDs are data, never path fragments.
    root = external(initialize() / "project-revisions" / new_id("revision"))
    revision.setdefault("change_log", []).append({
        "created_at": now(), "reason": reason.strip(), "impact": impact,
        "previous_sha256": hashlib.sha256(previous_bytes).hexdigest(),
        "edited_sha256": hashlib.sha256(current_bytes).hexdigest()})
    validate_project(revision)
    root.mkdir(parents=True, exist_ok=False)
    (root / "previous-project.json").write_bytes(previous_bytes)
    (root / "edited-project.json").write_bytes(current_bytes)
    path = write_new(root / "project.json", revision)
    write_new(root / "impact.json", impact)
    write_new(root / "manifest.json", {"project_id": current["id"], "created_at": now(),
        "files": [{"path": name, "sha256": digest(root / name)} for name in
                  ("previous-project.json", "edited-project.json", "project.json", "impact.json")],
        "limitations": ["仅归档项目记录；未复制外部证据或 CAD，不代表冻结工程或审核通过。"]})
    return path


def project_review(project, project_path: Path | None = None) -> dict:
    validate_project(project)
    findings = []

    def missing(stage, target, message):
        findings.append({"stage": stage, "object": target, "message": message})

    for field in ("scope", "requirements"):
        if not project[field]:
            missing("framing", "project", f"尚未填写 {field}")
    for question in project.get("open_questions", []):
        missing("framing", "project", "未决问题：" + question)
    for req in project["requirements"]:
        key = "requirements/" + req["id"]
        if not text_value(req.get("acceptance_criteria")):
            missing("framing", key, "缺少可判断的 acceptance_criteria")
        if req["verification_method"] == "tbd":
            missing("framing", key, "验证方法待定")
        allocated = any(req["id"] in module.get("requirement_ids", []) for module in project["modules"])
        if not allocated:
            missing("architecture", key, "需求尚未分配给模块")
        checks = [v for v in project["verifications"] if req["id"] in v.get("requirement_ids", [])]
        if not checks:
            missing("design", key, "缺少验证计划")
        if not any(v["result"] == "pass" for v in checks):
            missing("released", key, "没有通过的验证记录；需工程人员判断组合覆盖是否充分")
        if any(v["result"] in {"fail", "blocked"} for v in checks):
            missing("released", key, "仍有失败或阻塞的验证记录")
    if not project["modules"]:
        missing("architecture", "project", "尚未拆分模块")
    for module in project["modules"]:
        for field in ("name", "responsibility"):
            if not text_value(module.get(field)):
                missing("architecture", "modules/" + module["id"], "缺少 " + field)
    for interface in project["interfaces"]:
        if not text_value(interface.get("contract")):
            missing("architecture", "interfaces/" + interface["id"], "缺少电气/时序/机械接口 contract")
    if not project["budgets"] and not text_value(project.get("budget_scope_record")):
        missing("architecture", "project", "缺少关键预算，或明确说明哪些预算适用/不适用")
    for budget in project["budgets"]:
        if not text_value(budget.get("analysis")):
            missing("design", "budgets/" + budget["id"], "缺少预算推导与余量分析")
    for decision in project["decisions"]:
        if decision["status"] in {"open", "revisit"}:
            missing("design", "decisions/" + decision["id"], "方案尚未确定或需要重议")
        elif decision["status"] == "selected" and not text_value(decision.get("rationale")):
            missing("design", "decisions/" + decision["id"], "已选择方案缺少理由")
    for risk in project.get("risks", []):
        if risk["status"] in {"open", "mitigating"}:
            missing("design", "risks/" + risk["id"], "风险仍未收敛")
        elif not text_value(risk.get("disposition_record")):
            missing("design", "risks/" + risk["id"], "缺少风险关闭/接受记录")
    tasks_by_id = {t["id"]: t for t in project["tasks"]}
    task_reports = []
    if not tasks_by_id:
        missing("design", "project", "尚未拆解可执行任务")
    for task in project["tasks"]:
        key = "tasks/" + task["id"]
        blockers = [dep for dep in task.get("depends_on", []) if tasks_by_id[dep]["status"] != "done"]
        linked = "not_linked"
        if "task_path" in task:
            try:
                path = Path(task["task_path"])
                if not path.is_absolute():
                    if project_path is None:
                        raise ValueError("相对 task_path 需要项目档案路径")
                    path = project_path.resolve().parent / path
                record = read_json(path)
                validate_task(record)
                if record["id"] != task.get("task_id"):
                    raise ValueError("关联 task_id 与任务档案不一致")
                if not task.get("task_sha256") or digest(path) != task["task_sha256"]:
                    raise ValueError("缺少关联哈希或任务档案已改变，须重新核对")
                linked = "matched"
            except (OSError, ValueError, TypeError, KeyError) as exc:
                linked = "needs_review"
                missing("design", key, str(exc))
        if blockers and task["status"] in {"ready", "in_progress", "done"}:
            missing("design", key, "任务状态与未完成依赖矛盾：" + ", ".join(blockers))
        for field in ("goal", "deliverable", "acceptance_criteria"):
            if not text_value(task.get(field)):
                missing("design", key, "缺少 " + field)
        task_reports.append({"id": task["id"], "declared_status": task["status"],
                             "blocked_by": blockers, "linked_task": linked,
                             "dependencies_satisfied": not blockers})
    for verification in project["verifications"]:
        key = "verifications/" + verification["id"]
        if not verification.get("requirement_ids"):
            missing("design", key, "验证未关联需求")
        for field in ("conditions", "acceptance_criteria"):
            if not text_value(verification.get(field)):
                missing("design", key, "缺少 " + field)
        if verification["method"] == "tbd":
            missing("design", key, "验证方法尚未确定")
        if verification["result"] in {"pass", "fail"}:
            for field in ("evidence", "tested_version", "execution_record"):
                if not text_value(verification.get(field)):
                    missing("verification", key, "结果缺少 " + field)
    for stage, field in (("integration", "integration_record"),
                         ("verification", "validation_record"), ("released", "release_record")):
        if not text_value(project.get(field)):
            missing(stage, "project", "缺少 " + field + "；需记录版本、检查范围、结果及决定")
    for key in pending_reviews(project):
        missing("design", key, "受变更影响，尚未完成复核")
    active_reviews = pending_reviews(project)
    review_targets = {key: review_digest(project, key) for key in active_reviews}
    gates = {stage: {"record_review": "needs_work" if any(item["stage"] == stage for item in findings) else "no_recorded_gaps",
                     "finding_count": sum(item["stage"] == stage for item in findings)}
             for stage in sorted(PROJECT_STATES)}
    return {"project_id": project["id"], "status": project["status"], "findings": findings,
            "stage_record_reviews": gates, "tasks": task_reports, "review_targets": review_targets,
            "engineering_approval": "not_assessed",
            "limitations": ["仅报告记录缺项、显式依赖与关联任务哈希；不判定阶段通过或工程正确。",
                            "技术数值、测试充分性与证据原件真实性需人工/专项工具核验。"]}
