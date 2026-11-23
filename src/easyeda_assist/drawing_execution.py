"""Recorded consent, preflight and fail-closed single-use drawing execution.

Consent records describe real conversation evidence, not authenticated human
identity. The agent must show the concrete plan and record actual user assent.
Locks coordinate this tool in one persistent data root, not the CAD editor.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
from pathlib import Path

from .drawing import review_drawing_plan
from .drawing_api import MAX_OPERATIONS, comparable_source, validate_snapshot
from .project import object_digest, text_value
from .drawing_library import load_mapping_evidence, read_mapping_evidence, verify_mapping
from .storage import digest, external, initialize, new_id, now, read_json, write_new
from .drawing_circuit import CONTRACT, audit_circuit, library_evidence, validate_circuit, verify_reply
from .operations import V2, SOURCE, INCREMENTAL, BRANCH, ADAPTERS, adapter_for, validate_parameters as validate_registered, execute_once, require_dispatch
from .traceability import execution_project_digest, validate_origin

ADAPTER = V2


def _durable_new(path: Path, value: dict) -> Path:
    path = external(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def _durable_replace(path: Path, value: dict) -> Path:
    temporary = path.with_name(path.name + "." + new_id("tmp"))
    _durable_new(temporary, value)
    os.replace(temporary, path)
    return path


def capture_snapshot(bridge, window_id: str | None = None) -> Path:
    snapshot = bridge.drawing_snapshot(window_id)
    validate_snapshot(snapshot)
    return write_new(initialize() / "drawing-snapshots" / (new_id("source") + ".json"), snapshot)


def preflight(project: dict, plan: dict, *, for_execution=True) -> tuple[dict, dict]:
    report = review_drawing_plan(project, plan)
    problems = report["findings"] + report["policy_findings"]
    if problems:
        raise ValueError("方案不能执行：" + "；".join(problems))
    if len(plan["operations"]) > MAX_OPERATIONS:
        raise ValueError(f"单方案最多 {MAX_OPERATIONS} 个操作，请按可复核批次拆分")
    adapter = adapter_for(plan)
    circuit = adapter == CONTRACT
    incremental = adapter in {INCREMENTAL, BRANCH}
    if adapter == SOURCE and (plan["schema_version"] != 2 or len(plan["operations"]) != 1):
        raise ValueError("Source首期契约要求v2计划及一次已有器件属性修改")
    if plan["schema_version"] == 2:
        validate_origin(project, plan["origin"])
    if for_execution:
        require_dispatch(adapter)
    for op in plan["operations"]:
        if set(op) != {"id", "operation", "target", "before", "after", "expected_result", "parameters"}:
            raise ValueError("可执行操作仅允许六个说明字段和 parameters，不接受脚本或其他字段")
        validate_registered(adapter, op)
    target = plan["target"]
    snapshot_path = Path(target["snapshot_path"])
    if not snapshot_path.is_absolute():
        raise ValueError("snapshot_path 必须为外部绝对路径")
    snapshot_path = external(snapshot_path)
    raw = snapshot_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != target["snapshot_sha256"]:
        raise ValueError("输入快照哈希与方案不一致")
    snapshot = json.loads(raw.decode("utf-8-sig"))
    validate_snapshot(snapshot)
    identity = snapshot["identity"]
    if any(target[k] != snapshot[k] for k in ("window_id", "domain")) or any(
        target[k] != identity[k] for k in ("project_uuid", "document_uuid")
    ):
        raise ValueError("方案目标与完整文档源快照不一致")
    if adapter == BRANCH:
        from .drawing_branch import validate_branch
        geometry = validate_branch(plan, snapshot)
    elif incremental:
        from .drawing_incremental import validate_incremental
        geometry = validate_incremental(plan, snapshot)
    elif circuit:
        geometry = validate_circuit(plan, snapshot)
    elif adapter == SOURCE:
        from .source_properties import prepare
        for op in plan["operations"]:
            prepare(snapshot, op["parameters"])
    else:
        for op in plan["operations"]:
            load_mapping_evidence(snapshot, op["parameters"])
    binding = {"adapter": adapter, "project_id": project["id"],
               "project_sha256": execution_project_digest(project) if plan["schema_version"] == 2 else object_digest(project),
               "plan_sha256": report["plan_sha256"],
               "snapshot_sha256": target["snapshot_sha256"]}
    if plan["schema_version"] == 2:
        binding["binding_version"] = 2
    preview = {**binding, "target": {"window_id": snapshot["window_id"], **identity},
               "request": plan["request"], "evidence": copy.deepcopy(plan["evidence"]),
               "readback_plan": plan["readback_plan"], "failure_plan": plan["failure_plan"],
               "operations": copy.deepcopy(plan["operations"]),
               "limitations": ["原理图新增器件、网络标号和导线；不保存、不自动回滚；位置单位 10mil。" if circuit else "仅放置原理图器件；不连线、不保存、不自动回滚；位置单位 10mil。",
                               "本地预检不证明工程正确或用户认可；执行时还会比对实时完整文档源。",
                               "API 为 beta；编辑器没有事务锁，执行期间请勿切换文档或同时编辑。"]}
    if circuit or incremental:
        preview["connectivity"] = copy.deepcopy(plan["connectivity"])
        preview["library_geometry"] = geometry["library_geometry"]
        preview["limitations"].append("仅支持无总线页，或精确声明基线器件、网络标号和导线；失败保留现场。")
    if incremental:
        preview["edit_region"] = copy.deepcopy(plan["edit_region"])
        preview["incremental_baseline"] = copy.deepcopy(plan["incremental_baseline"])
        preview["baseline_component_count"] = geometry["baseline_component_count"]
        preview["baseline_drc_count"] = geometry["baseline_drc_count"]
        preview["sheet_frame"] = geometry["sheet_frame"]
        preview["limitations"] = ["非空白页中新增独立模块，旧源记录、旧网表器件与详细DRC均须保持；不保存、不自动回滚。",
                                   "当前不允许连接既有网络、移动/删除已有对象或合并既有导线。",
                                   "操作核验不等于全部派生数据刷新或工程功能验收。"]
    if adapter == SOURCE:
        preview["limitations"] = ADAPTERS[SOURCE]["limitations"][:]
    if adapter == BRANCH:
        preview["branch_anchors"] = copy.deepcopy(plan["branch_anchors"])
        preview["limitations"] = ADAPTERS[BRANCH]["limitations"][:]
    if plan["schema_version"] == 2:
        preview["capability"] = copy.deepcopy(ADAPTERS[adapter])
        preview["origin"] = copy.deepcopy(plan["origin"])
    checked = {"binding": binding, "preview": preview}
    if not for_execution and "layout_review" in report:
        # Advisory output stays outside the preview bound into consent records.
        checked["layout_review"] = report["layout_review"]
    if not for_execution and (circuit or incremental):
        from .drawing_readability import route_metrics
        metrics = route_metrics(plan["operations"])
        if metrics is not None:
            checked["route_metrics"] = metrics
    return checked, snapshot


def approve_plan(project: dict, plan: dict, shown_sha256: str, user_message: str, reference: str) -> Path:
    checked, _ = preflight(project, plan)
    if shown_sha256 != checked["binding"]["plan_sha256"]:
        raise ValueError("当前方案与已展示方案摘要不一致，请先展示更新后的方案")
    if not text_value(user_message) or not text_value(reference):
        raise ValueError("须记录真实用户认可原话和对话引用；不得用笼统开发授权代替")
    record = {"schema_version": 1, "kind": "drawing-consent", "created_at": now(),
              **checked["binding"], "user_message": user_message.strip(), "reference": reference.strip(),
              "preview": checked["preview"],
              "limitation": "由调用方记录真实对话认可；CLI 不验证发言者身份或自然语言意图。"}
    return _durable_new(initialize() / "drawing-approvals" / (new_id("approval") + ".json"), record)


def _verify_consent(checked: dict, approval: dict) -> None:
    if not isinstance(approval, dict) or type(approval.get("schema_version")) is not int or approval["schema_version"] != 1 or approval.get("kind") != "drawing-consent":
        raise ValueError("缺少独立的方案认可记录；plan.approval 不构成认可")
    if any(approval.get(k) != v for k, v in checked["binding"].items()) or approval.get("preview") != checked["preview"]:
        raise ValueError("认可记录已失效：项目、策略、方案、快照或适配器已变化")
    if not text_value(approval.get("user_message")) or not text_value(approval.get("reference")):
        raise ValueError("认可记录缺少真实用户原话或对话引用")


def _verified_snapshot(reply: dict, before: dict, parameters: dict) -> dict:
    if reply.get("status") != "verified" or reply.get("attempted") is not True:
        raise ValueError("写入结果没有通过回读")
    observed = reply.get("observed")
    primitive_id = reply.get("primitive_id")
    if not text_value(primitive_id) or not isinstance(observed, dict) or observed.get("primitive_id") != primitive_id:
        raise ValueError("回读图元 ID 无效")
    expected = {key: parameters[key] for key in (
        "library_uuid", "x", "y", "mirror", "add_into_bom", "add_into_pcb", "sub_part_name")}
    expected.update(device_uuid=parameters["expected_project_device_uuid"], rotation=parameters["expected_canvas_rotation"])
    for key, value in expected.items():
        if key not in observed or (type(observed[key]) is bool) != (type(value) is bool) or observed[key] != value:
            raise ValueError("回读字段不一致：" + key)
    snapshot = {**before, "captured_at": now(), "source": reply.get("source"), "identity": reply.get("identity")}
    validate_snapshot(snapshot)
    if snapshot["identity"] != before["identity"] or comparable_source(snapshot["source"]) == comparable_source(before["source"]):
        raise ValueError("写入后目标或文档源未通过核验")
    verify_mapping(reply.get("project_evidence"), snapshot, parameters)
    pins = reply.get("pins")
    if (not isinstance(pins, list) or len(pins) != len(parameters["expected_pins"]) or
            any(not isinstance(pin, dict) or not text_value(pin.get("primitive_id")) for pin in pins) or
            len({pin["primitive_id"] for pin in pins}) != len(pins)):
        raise ValueError("引脚回读数量或身份不一致")
    for pin in parameters["expected_pins"]:
        found = [p for p in pins if p.get("number") == pin["number"]]
        if len(found) != 1 or any(type(found[0].get(k)) not in (int, float) or found[0][k] != pin[k] for k in ("x", "y")):
            raise ValueError("引脚回读位置不一致：" + pin["number"])
    return snapshot


def execute_plan(project: dict, plan: dict, approval_path: Path, bridge_factory) -> dict:
    checked, snapshot = preflight(project, plan)
    approval_path = external(approval_path)
    approval = read_json(approval_path)
    _verify_consent(checked, approval)
    root = initialize()
    binding = checked["binding"]
    run_dir = external(root / "drawing-executions" / binding["plan_sha256"])
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir()
    except FileExistsError as exc:
        raise ValueError("方案已被领取执行，禁止重放；先检查现场与日志：" + str(run_dir)) from exc
    claim = {"created_at": now(), **binding, "approval_path": str(approval_path),
             "approval_sha256": digest(approval_path), "pid": os.getpid()}
    _durable_new(run_dir / "claim.json", claim)
    _durable_new(run_dir / "project.json", project)
    _durable_new(run_dir / "plan.json", plan)
    _durable_new(run_dir / "approval.json", approval)
    _durable_new(run_dir / "input-snapshot.json", snapshot)
    origin_task = (plan.get("origin") or {}).get("task")
    if origin_task:
        raw_task = external(Path(origin_task["path"])).read_bytes()
        if hashlib.sha256(raw_task).hexdigest() != origin_task["sha256"]:
            raise ValueError("归档前原任务发生变化，未执行CAD写入")
        _durable_new(run_dir / "origin-task-evidence.json", {
            "reference": origin_task, "raw_base64": base64.b64encode(raw_task).decode("ascii")})
    circuit = plan.get("execution_contract") == CONTRACT
    adapter = binding["adapter"]
    incremental = adapter in {INCREMENTAL, BRANCH}
    baseline_bundle = None
    if incremental:
        from .drawing_incremental import load_baseline
        baseline_bundle, _, _ = load_baseline(plan, snapshot)
        _durable_new(run_dir / "incremental-baseline.json", baseline_bundle)
    for index, operation in enumerate(plan["operations"]):
        if circuit or incremental:
            if operation["operation"] != "place_component":
                continue
            evidence, _ = library_evidence(operation["parameters"])
        elif adapter != SOURCE:
            evidence, _ = read_mapping_evidence(snapshot, operation["parameters"])
        else:
            continue
        _durable_new(run_dir / f"{index + 1:03d}-mapping-input.json", evidence)
    lock_key = {k: snapshot["identity"][k] for k in ("project_uuid", "document_uuid")}
    lock_dir = external(root / "drawing-locks" / object_digest(lock_key))
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    if not incremental and any((lock_dir / name).exists() for name in
           ("property-repair-claim.json", "layout-repair-claim.json")):
        raise ValueError("目标已有属性或布局返工记录；先核对该返工与新快照，禁止沿旧绘制计划继续写入")
    recovery_of = plan.get("recovery_of")
    if incremental:
        from .drawing_incremental import claim_lock
        incremental_claim, prior_locks = claim_lock(lock_dir, run_dir, claim, plan.get("reviewed_lock_sha256"))
    elif recovery_of is not None:
        old_dir = external(Path(recovery_of))
        if not old_dir.is_absolute() or not old_dir.is_dir():
            raise ValueError("recovery_of 必须指向已存在的旧执行目录")
        owner_path, old_result_path, old_claim_path = lock_dir / "owner.json", old_dir / "result.json", old_dir / "claim.json"
        if not owner_path.is_file() or not old_result_path.is_file() or not old_claim_path.is_file():
            raise ValueError("恢复要求旧目标锁、claim 和 result 均存在")
        owner, old_result, old_claim = read_json(owner_path), read_json(old_result_path), read_json(old_claim_path)
        old_snapshot = read_json(old_dir / "input-snapshot.json")
        if (old_result.get("status") != "uncertain" or
                old_claim.get("adapter") != CONTRACT or old_snapshot.get("identity") != snapshot["identity"] or
                old_claim.get("plan_sha256") != old_dir.name):
            raise ValueError("旧执行锁不是与本次目标绑定的 uncertain run")
        recovery_path = lock_dir / "recovery-run.json"
        if recovery_path.exists():
            active = read_json(recovery_path)
            active_dir = Path(active.get("run_dir", "")).resolve()
            active_lineage = read_json(active_dir / "recovery-lineage.json") if (active_dir / "recovery-lineage.json").is_file() else {}
            active_result = read_json(active_dir / "result.json") if (active_dir / "result.json").is_file() else {}
            cursor = active_dir
            owner_dir = Path(owner.get("run_dir", "")).resolve()
            chain_ok = False
            for _ in range(20):
                if cursor == owner_dir:
                    chain_ok = True
                    break
                lineage_path = cursor / "recovery-lineage.json"
                result_path = cursor / "result.json"
                if not lineage_path.is_file() or not result_path.is_file() or read_json(result_path).get("status") != "uncertain":
                    break
                lineage = read_json(lineage_path)
                if Path(lineage.get("recovery_run", "")).resolve() != cursor:
                    break
                cursor = Path(lineage.get("recovered_run", "")).resolve()
            if (active_dir != old_dir.resolve() or Path(active_lineage.get("recovery_run", "")).resolve() != active_dir or
                    active_result.get("status") != "uncertain" or not chain_ok):
                raise ValueError("该 uncertain 锁已有其他恢复尝试，须先人工核对执行结果")
            _durable_replace(recovery_path, {"run_dir": str(run_dir), **claim})
        else:
            if Path(owner.get("run_dir", "")).resolve() != old_dir.resolve():
                raise ValueError("旧执行记录不是当前锁的持有人")
            _durable_new(recovery_path, {"run_dir": str(run_dir), **claim})
        lineage = {"recovered_run": str(old_dir), "recovery_run": str(run_dir),
                   "old_plan_sha256": old_claim["plan_sha256"], "new_plan_sha256": binding["plan_sha256"]}
        _durable_new(run_dir / "recovery-lineage.json", lineage)
    else:
        try:
            lock_dir.mkdir()
        except FileExistsError as exc:
            raise ValueError("目标有执行中或状态未知的任务；先核对现场和锁记录：" + str(lock_dir)) from exc
        _durable_new(lock_dir / "owner.json", {"run_dir": str(run_dir), **claim})
    status = "blocked"
    steps = []
    error = None
    try:
        bridge = bridge_factory()
        result_primitives = {}
        if incremental:
            from .drawing_incremental import check_live_baseline
            check_live_baseline(bridge, baseline_bundle, snapshot)
        designators = {item["primitive_id"]: item["designator"] for item in plan.get("base_components", [])}
        if circuit:
            live = bridge.drawing_components(snapshot["window_id"])
            items = live.get("components")
            baseline = {item["primitive_id"]: item for item in plan.get("base_components", [])}
            baseline_flags = {item["primitive_id"]: item for item in plan.get("base_flags", [])}
            if (live.get("identity") != snapshot["identity"] or comparable_source(live.get("source", "")) != comparable_source(snapshot["source"]) or
                    live.get("truncated") is not False or not isinstance(items, list)):
                raise ValueError("实时目标与恢复快照不一致")
            sheets = [item for item in items if item.get("component_type") == "sheet"]
            parts = {item.get("primitive_id"): item for item in items if item.get("component_type") == "part"}
            flags = {item.get("primitive_id"): item for item in items if item.get("component_type") == "netflag"}
            if len(sheets) > 1 or set(parts) != set(baseline) or set(flags) != set(baseline_flags):
                raise ValueError("实时器件不精确匹配图框与声明的恢复基线")
            for pid, expected in baseline.items():
                observed = parts[pid]
                if (observed.get("component", {}).get("libraryUuid") != expected["library_uuid"] or
                        observed.get("component", {}).get("uuid") != expected["project_device_uuid"] or
                        observed.get("designator") != expected["designator"] or
                        observed.get("x") != expected["x"] or observed.get("y") != expected["y"] or
                        observed.get("rotation") != expected["expected_canvas_rotation"]):
                    raise ValueError("实时恢复基线器件身份或位置不匹配")
            for pid, expected in baseline_flags.items():
                observed = flags[pid]
                if (observed.get("component", {}).get("uuid") != expected["project_device_uuid"] or
                        observed.get("x") != expected["x"] or observed.get("y") != expected["y"]):
                    raise ValueError("实时恢复基线网络标号身份或位置不匹配")
        for index, operation in enumerate(plan["operations"]):
            if incremental:
                from .drawing_incremental import verify_lock
                verify_lock(lock_dir, incremental_claim, prior_locks)
            prefix = f"{index + 1:03d}"
            _durable_new(run_dir / (prefix + "-intent.json"), {
                "created_at": now(), "operation": operation,
                "source_sha256": object_digest(snapshot["source"]), "target": snapshot["identity"]})
            # Once the request could have left this process, errors are uncertain.
            status = "uncertain"
            reply = execute_once(adapter, bridge, snapshot, operation)
            _durable_new(run_dir / (prefix + "-reply.json"), reply)
            if not isinstance(reply, dict):
                raise ValueError("写入返回格式不兼容")
            if reply.get("status") == "blocked" and reply.get("attempted") is False:
                status = "partial" if steps else "blocked"
                error = str(reply.get("error", "执行前核对失败"))
                steps.append({"id": operation["id"], "status": status})
                break
            if reply.get("status") != "verified":
                raise ValueError(str(reply.get("error", "写入或回读状态未知")))
            if adapter == BRANCH:
                from .drawing_branch import verify_branch_reply
                snapshot = verify_branch_reply(reply, snapshot, operation, plan)
            elif incremental:
                from .drawing_incremental import verify_incremental_reply
                snapshot = verify_incremental_reply(reply, snapshot, operation)
            elif adapter == SOURCE:
                from .source_properties import verify_reply as verify_source_reply
                snapshot = verify_source_reply(reply, snapshot, operation["parameters"])
            else:
                snapshot = verify_reply(reply, snapshot, operation) if circuit else _verified_snapshot(reply, snapshot, operation["parameters"])
            if circuit and operation["operation"] == "place_component":
                designators[operation["id"]] = reply["observed"]["designator"]
            if incremental and (operation["operation"] == "place_component" or adapter == BRANCH):
                result_primitives[operation["id"]] = reply["primitive_id"]
            _durable_new(run_dir / (prefix + "-snapshot.json"), snapshot)
            steps.append({"id": operation["id"], "status": "verified", "primitive_id": reply["primitive_id"]})
            # The completed step is known. Failures before the next request
            # (e.g. intent logging) leave a partial result, not an unknown write.
            status = "partial"
        else:
            if circuit or incremental:
                # Auditing is read-only; failure still means a written result is uncertain.
                status = "uncertain"
                audit = bridge.drawing_circuit_audit(snapshot)
                _durable_new(run_dir / "final-audit-raw.json", audit)
                if incremental:
                    from .drawing_incremental import audit_incremental, verify_lock
                    if adapter == BRANCH:
                        from .drawing_branch import audit_branch
                        summary = audit_branch(audit, snapshot, plan, baseline_bundle, result_primitives)
                    else:
                        summary = audit_incremental(audit, snapshot, plan, baseline_bundle, result_primitives)
                    verify_lock(lock_dir, incremental_claim, prior_locks)
                else:
                    summary = audit_circuit(audit, snapshot, {**plan, "result_designators": designators})
                _durable_new(run_dir / "final-audit.json", summary)
                if not summary["connectivity_matches"]:
                    status = "verification_failed"
                    raise ValueError("最终网表连接与方案不一致")
                if not summary["drc_passed"]:
                    status = "verification_failed"
                    raise ValueError("最终 DRC 存在违规，请检查审计记录")
            status = "verified"
    except Exception as exc:
        # No retry or rollback: timed-out EDA calls may still be running.
        error = str(exc)
    result = {"status": status, "completed": len([s for s in steps if s["status"] == "verified"]),
              "total": len(plan["operations"]), "steps": steps, "error": error,
              "run_dir": str(run_dir), "target_lock": str(lock_dir),
              "retry_allowed": False, "finished_at": now()}
    result.update(plan_sha256=binding["plan_sha256"], project_id=project["id"],
                  origin=copy.deepcopy(plan.get("origin")), engineering_validation="not-established-by-cad-execution")
    _durable_new(run_dir / "result.json", result)
    if incremental and status in {"verified", "blocked"}:
        from .drawing_incremental import release_lock
        release_lock(lock_dir, incremental_claim, prior_locks)
    elif status == "verified" or (status == "blocked" and recovery_of is None):
        if recovery_of is not None:
            (lock_dir / "recovery-run.json").unlink()
        (lock_dir / "owner.json").unlink()
        lock_dir.rmdir()
    return result
