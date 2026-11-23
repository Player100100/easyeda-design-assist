"""Single-use, journaled property experiments on an explicitly authorized target.

General Source dispatch stays closed. A reviewed historical lock can host a
separate experiment claim; its owner and past results are never changed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

from .design_audit import compare_audits
from .drawing_execution import _durable_new
from .drawing_source import records
from .operations import SOURCE
from .project import object_digest
from .source_properties import _fixed_property_script, verify_reply
from .source_validation import _pair, check_validation, load_case
from .storage import external, initialize, new_id, now, read_json
from .traceability import canonical_digest

ACTIVE_CLAIM = "source-trial-active.json"


def lock_inventory(lock_dir):
    files = []
    if lock_dir.exists():
        for path in sorted(lock_dir.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise ValueError("目标锁包含未知目录或链接，须先检查现场")
            files.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return files, canonical_digest(files)


def review_trial_state(run_dir, source_path, audit_path):
    """Describe all observed source changes for an explicit investigator review."""
    run_dir = external(Path(run_dir))
    case, baseline, _ = load_case(run_dir / "case.json")
    if run_dir.name != canonical_digest(case):
        raise ValueError("实验目录与方案摘要不一致")
    source, audit = read_json(external(Path(source_path))), read_json(external(Path(audit_path)))
    before, after = _pair(*baseline), _pair(source, audit)
    if (after[1]["identity"] != case["identity"] or after[1]["window_id"] != case["window_id"] or
            after[0]["target"] != case["parameters"]["source_target"] or
            after[0]["primitive_id"] != case["parameters"]["primitive_id"] or after[4] != before[4]):
        raise ValueError("复核证据与原实验目标不一致")
    if any(after[2]["properties"].get(key) != value or after[5]["props"].get(key) != value
           for key, value in case["parameters"]["patch"].items()):
        raise ValueError("源和网表尚未观察到实验属性，不能关闭未知状态")
    old_rows, new_rows = records(before[1]["source"]), records(after[1]["source"])
    comparison = compare_audits(before[3], after[3])
    # Capture timestamps/provenance change on each read. Bind this review to the
    # state and full netlist bytes, while retaining raw captures in the journal.
    for side in ("before", "after"):
        comparison[side].pop("audit_sha256")
    review = {"case_sha256": canonical_digest(case), "before_source_sha256": case["before_source_sha256"],
              "after_source_sha256": after[3]["source_sha256"],
              "source_changes": [{"id": rid, "before": old_rows.get(rid), "after": new_rows.get(rid)}
                                 for rid in sorted(old_rows.keys() | new_rows.keys()) if old_rows.get(rid) != new_rows.get(rid)],
              "component_before": before[2], "component_after": after[2],
              "after_netlist_sha256": after[3]["netlist_sha256"],
              "comparison": comparison}
    return review, canonical_digest(review)


def close_reviewed_trial(run_dir, source_path, audit_path, reviewed_sha256, review_message, reference, bridge_factory):
    """Close only this experiment's active claim after inspecting the known state.

The old uncertain result stays immutable and does not become verified. This
requires an explicit review of the complete delta and a matching live scene.
"""
    run_dir = external(Path(run_dir))
    review, review_sha = review_trial_state(run_dir, source_path, audit_path)
    if review_sha != reviewed_sha256 or not all(isinstance(s, str) and s.strip() for s in (review_message, reference)):
        raise ValueError("须提供与完整现场差异一致的复核摘要、判断和依据引用")
    case, _, _ = load_case(run_dir / "case.json")
    result = read_json(run_dir / "result.json")
    if result.get("status") not in {"uncertain", "observation_failed"} or result.get("case_sha256") != review["case_sha256"]:
        raise ValueError("仅可复核本实验已留档的未知或观察未通过结果")
    root = initialize()
    key = {k: case["identity"][k] for k in ("project_uuid", "document_uuid")}
    lock_dir = external(root / "drawing-locks" / object_digest(key))
    expected = read_json(run_dir / "claim.json")
    if read_json(lock_dir / ACTIVE_CLAIM) != expected:
        raise ValueError("实验锁持有人不匹配")
    bridge = bridge_factory()
    health = bridge.health()
    if type(health.get("pendingRequests")) is not int or health["pendingRequests"] != 0:
        raise ValueError("仍有待处理请求，不能关闭实验锁")
    live_source = bridge.design_query("source-component", case["window_id"], case["parameters"]["primitive_id"])
    live_audit = bridge.design_query("audit", case["window_id"])
    evidence_dir = run_dir / "reviews" / new_id("review")
    source_evidence = _durable_new(evidence_dir / "source.json", live_source)
    audit_evidence = _durable_new(evidence_dir / "audit.json", live_audit)
    live_review, live_sha = review_trial_state(run_dir, source_evidence, audit_evidence)
    if live_sha != reviewed_sha256:
        raise ValueError("现场已不符合被复核的完整差异，保留实验锁")
    resolution = {"kind": "source-trial-resolution", "created_at": now(), "status": "reviewed-known-state",
                  "case_sha256": review["case_sha256"], "original_status": result["status"],
                  "reviewed_sha256": review_sha, "review_message": review_message, "reference": reference,
                  "review": live_review, "review_evidence_dir": str(evidence_dir),
                  "verification_passed": False, "drc_freshness_established": False,
                  "general_source_dispatch_enabled": False}
    path = _durable_new(run_dir / "resolution.json", resolution)
    if read_json(lock_dir / ACTIVE_CLAIM) != expected:
        raise ValueError("复核结束时实验锁持有人变化，保留现场")
    (lock_dir / ACTIVE_CLAIM).unlink()
    return path


def execute_trial(case_path, shown_sha256, user_message, reference, bridge_factory,
                  reviewed_lock_sha256=None, pause=time.sleep, backend="source"):
    if backend not in {"source", "canvas-attribute"}:
        raise ValueError("未知实验属性后端")
    case, baseline, archived = load_case(case_path)
    case_sha = canonical_digest(case)
    if shown_sha256 != case_sha:
        raise ValueError("实验方案与已展示摘要不一致")
    if not all(isinstance(value, str) and value.strip() for value in (user_message, reference)):
        raise ValueError("实验须记录真实测试授权原话和对话引用")
    root = initialize()
    run_dir = external(root / "source-trials" / case_sha)
    lock_key = {key: case["identity"][key] for key in ("project_uuid", "document_uuid")}
    lock_dir = external(root / "drawing-locks" / object_digest(lock_key))
    prior, prior_sha = lock_inventory(lock_dir)
    if any(item["file"] == ACTIVE_CLAIM for item in prior):
        raise ValueError("目标已有未关闭的Source实验，须先核对现场，禁止重试")
    if prior and reviewed_lock_sha256 != prior_sha:
        raise ValueError("目标保留历史锁；须先审阅其现场与记录，并传入当前锁清单摘要：" + prior_sha)
    if run_dir.exists():
        raise ValueError("实验已领取，禁止重放：" + str(run_dir))
    bridge = bridge_factory()
    health = bridge.health()
    if type(health.get("pendingRequests")) is not int or health["pendingRequests"] != 0:
        raise ValueError("Bridge仍有待处理请求，不能开始实验")
    live_source = bridge.design_query("source-component", case["window_id"], case["parameters"]["primitive_id"])
    live_audit = bridge.design_query("audit", case["window_id"])
    old = _pair(*baseline)
    live = _pair(live_source, live_audit)
    if (live[0]["target"] != case["parameters"]["source_target"] or live[0]["primitive_id"] != case["parameters"]["primitive_id"] or
            live[1]["identity"] != case["identity"] or live[1]["window_id"] != case["window_id"] or
            live[3]["source_sha256"] != case["before_source_sha256"] or live[2] != old[2] or
            live[3]["netlist_sha256"] != old[3]["netlist_sha256"] or
            sorted(map(canonical_digest, live[3]["drc"])) != sorted(map(canonical_digest, old[3]["drc"]))):
        raise ValueError("实时Source、网表或DRC基线已变化，先重新准备实验")
    snapshot = live[1]
    script = _fixed_property_script(snapshot, case["parameters"], backend)
    if lock_inventory(lock_dir)[1] != prior_sha:
        raise ValueError("实验准备期间目标锁变化，禁止提交")
    run_dir.mkdir(parents=True, exist_ok=False)
    authorization = {"kind": "source-trial-authorization", "created_at": now(), "case_sha256": case_sha,
                     "backend": backend,
                     "target": case["identity"], "user_message": user_message, "reference": reference,
                     "scope": "single-property-experiment-only", "prior_lock_inventory_sha256": prior_sha,
                     "general_source_dispatch_enabled": False}
    _durable_new(run_dir / "authorization.json", authorization)
    for name, raw in archived:
        with (run_dir / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    _durable_new(run_dir / "input-snapshot.json", snapshot)
    _durable_new(run_dir / "live-source-before.json", live_source)
    _durable_new(run_dir / "live-audit-before.json", live_audit)
    _durable_new(run_dir / "prior-lock-review.json", {"files": prior, "sha256": prior_sha, "bridge_health": health})
    lock_dir.mkdir(parents=True, exist_ok=True)
    claim = {"created_at": now(), "run_dir": str(run_dir), "case_sha256": case_sha,
             "adapter": SOURCE if backend == "source" else "canvas-attribute-trial", "backend": backend,
             "purpose": "authorized-experiment", "prior_lock_inventory_sha256": prior_sha}
    _durable_new(lock_dir / ACTIVE_CLAIM, claim)
    _durable_new(run_dir / "claim.json", claim)
    status, error, observation_path, attempted = "blocked", None, None, False
    try:
        current_files, _ = lock_inventory(lock_dir)
        if [item for item in current_files if item["file"] != ACTIVE_CLAIM] != prior:
            raise ValueError("目标历史锁已变化，不能写入")
        _durable_new(run_dir / "intent.json", {"created_at": now(), "case_sha256": case_sha,
                     "backend": backend,
                     "parameters": case["parameters"], "source_sha256": case["before_source_sha256"],
                     "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest()})
        status, attempted = "uncertain", True
        reply, window = bridge._execute_fixed(script, case["window_id"])
        _durable_new(run_dir / "reply.json", {"window_id": window, **reply})
        if window != case["window_id"]:
            raise ValueError("实验写入返回了不同窗口")
        if reply.get("status") == "blocked" and reply.get("attempted") is False:
            status, attempted = "blocked", False
            raise ValueError(str(reply.get("error", "提交前检查失败")))
        if backend == "canvas-attribute" and reply.get("backend") != backend:
            raise ValueError("实验回读后端不符")
        after = verify_reply(reply, snapshot, case["parameters"])
        _durable_new(run_dir / "source-snapshot-after.json", after)
        status = "observation_failed"
        # Only reads repeat. Never retry the write, even on transport errors.
        for sample in range(6):
            if sample:
                pause(1)
            prefix = f"{sample + 1:03d}"
            try:
                source = bridge.design_query("source-component", case["window_id"], case["parameters"]["primitive_id"])
                source_path = _durable_new(run_dir / (prefix + "-source-after.json"), source)
                audit = bridge.design_query("audit", case["window_id"])
                audit_path = _durable_new(run_dir / (prefix + "-audit-after.json"), audit)
                observation = check_validation(run_dir / "case.json", source_path, audit_path)
                observation_path = _durable_new(run_dir / (prefix + "-observation.json"), observation)
                if observation["property_observation_passed"]:
                    status = "property_observed"
                    break
            except Exception as exc:
                _durable_new(run_dir / (prefix + "-read-error.json"), {"error": str(exc), "captured_at": now()})
        if status != "property_observed":
            error = "属性写入已回读，但网表/DRC观察尚未满足保守检查；保留现场与实验锁。"
    except Exception as exc:
        error = str(exc)
    result = {"schema_version": 1, "kind": "source-trial-result", "case_sha256": case_sha,
              "backend": backend,
              "status": status, "attempted": attempted, "run_dir": str(run_dir), "target_lock": str(lock_dir),
              "observation_path": str(observation_path) if observation_path else None, "error": error,
              "finished_at": now(), "retry_allowed": False, "drc_freshness_established": False,
              "engineering_verified": False, "general_source_dispatch_enabled": False}
    _durable_new(run_dir / "result.json", result)
    if status in {"blocked", "property_observed"}:
        if canonical_digest(claim) != canonical_digest(read_json(lock_dir / ACTIVE_CLAIM)):
            raise ValueError("实验锁持有人变化，保留现场")
        (lock_dir / ACTIVE_CLAIM).unlink()
        if not any(lock_dir.iterdir()):
            lock_dir.rmdir()
    return result
