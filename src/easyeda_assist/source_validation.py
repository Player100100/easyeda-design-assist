"""Read-only preparation and evidence checks for one Source property trial.

A changed property in an export establishes only that property's observation.
It cannot establish DRC freshness, authorize a write or enable an adapter.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .design_audit import compare_audits, source_digest, unwrap_audit
from .drawing_api import validate_snapshot
from .drawing_properties import validate_patch, verify_property_patch
from .drawing_source import records
from .operations import SOURCE_VERSION
from .schematic import components_from_raw
from .source_properties import validate_parameters
from .storage import external, initialize, new_id, now, write_new
from .traceability import canonical_digest


def _source_query(value):
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1 or
            value.get("kind") != "design-query" or value.get("query") != "source-component"):
        raise ValueError("需要统一的source-component查询记录")
    data, target, provenance = (value.get(key) for key in ("data", "target", "provenance"))
    if not all(isinstance(item, dict) for item in (data, target, provenance)):
        raise ValueError("Source查询缺少数据、目标或来源")
    snapshot = {"schema_version": 1, "kind": "drawing-source", "domain": "schematic",
                "captured_at": value.get("captured_at"), "window_id": data.get("window_id"),
                "identity": data.get("identity"), "source": data.get("source")}
    validate_snapshot(snapshot)
    source_target = data.get("target")
    if (target.get("identity") != snapshot["identity"] or target.get("window_id") != snapshot["window_id"] or
            target.get("source_target") != source_target or provenance.get("editor_version") != data.get("editor_version")):
        raise ValueError("Source查询外壳与内层不一致")
    if data.get("editor_version") != SOURCE_VERSION:
        raise ValueError("Source验证仅覆盖当前已实现的客户端版本，须重新建立基线")
    validate_parameters({"primitive_id": data.get("primitive_id"), "patch": {"Name": "validation"},
                         "source_target": source_target})
    if source_target["projectId"] != snapshot["identity"]["project_uuid"]:
        raise ValueError("Source名称目标与工程身份不一致")
    outputs = data.get("outputs")
    output = outputs.get("component") if isinstance(outputs, dict) else None
    component = output.get("value") if isinstance(output, dict) else None
    if (not isinstance(output, dict) or output.get("status") != "returned" or
            not isinstance(component, dict) or component.get("primitiveId") != data["primitive_id"] or
            not isinstance(component.get("properties"), dict)):
        raise ValueError("Source器件详情未成功返回或身份不符")
    canonical_digest(value)
    return data, snapshot, component


def _pair(source_query, audit_query):
    data, snapshot, component = _source_query(source_query)
    audit = unwrap_audit(audit_query)
    if (audit["identity"] != snapshot["identity"] or audit["window_id"] != snapshot["window_id"] or
            audit["editor_version"] != data["editor_version"] or audit["source_sha256"] != source_digest(snapshot["source"])):
        raise ValueError("Source查询与审计不属于同一目标、版本和源状态")
    rows = records(snapshot["source"])
    pid = data["primitive_id"]
    if pid not in rows or rows[pid][0]["type"] != "COMPONENT":
        raise ValueError("Source源中没有对应的器件图元")
    attrs = {}
    for env, value in rows.values():
        if env["type"] == "ATTR" and value.get("parentId") == pid:
            key = value.get("key")
            if key in attrs:
                raise ValueError("Source器件属性重复")
            attrs[key] = value.get("value")
    identity_fields = ("Unique ID", "Designator", "Device")
    props = component["properties"]
    if any(not isinstance(props.get(key), str) or not props[key].strip() or props[key] != attrs.get(key)
           for key in identity_fields):
        raise ValueError("Source详情与源缺少一致的Unique ID、位号或Device身份")
    source_owners = [value.get("parentId") for env, value in rows.values()
                     if env["type"] == "ATTR" and value.get("key") == "Unique ID" and
                     value.get("value") == props["Unique ID"] and
                     rows.get(value.get("parentId"), ({}, {}))[0].get("type") == "COMPONENT"]
    if source_owners != [pid]:
        raise ValueError("Source源中的Unique ID不唯一，无法绑定网表组件")
    netlist = components_from_raw(audit["raw_netlist"])
    candidates = [(cid, item) for cid, item in netlist.items()
                  if item["props"].get("Unique ID") == props["Unique ID"]]
    if len(candidates) != 1:
        raise ValueError("网表Unique ID缺失或不唯一，不按位号猜测映射")
    cid, exported = candidates[0]
    if any(exported["props"].get(key) != props[key] for key in identity_fields):
        raise ValueError("网表与Source的器件身份不一致")
    return data, snapshot, component, audit, cid, exported


def make_case(source_query, audit_query, key, value):
    data, snapshot, component, audit, cid, exported = _pair(source_query, audit_query)
    patch = {key: value}
    parameters = {"primitive_id": data["primitive_id"], "patch": patch, "source_target": data["target"]}
    validate_parameters(parameters)
    attrs = validate_patch(snapshot, data["primitive_id"], patch)
    old = attrs[key]
    if component["properties"].get(key) != old or exported["props"].get(key) != old:
        raise ValueError("所选属性未在Source和基线网表中一致出现，不能用来观察导出更新")
    return {"schema_version": 1, "kind": "source-property-validation-case", "editor_version": data["editor_version"],
            "identity": deepcopy(snapshot["identity"]), "window_id": snapshot["window_id"],
            "parameters": deepcopy(parameters), "netlist_component_id": cid,
            "component_unique_id": component["properties"]["Unique ID"], "designator": component["properties"]["Designator"],
            "before_values": {key: old}, "before_source_sha256": audit["source_sha256"],
            "baseline_query_sha256": {"source": canonical_digest(source_query), "audit": canonical_digest(audit_query)},
            "cad_write_authorized": False, "requires_isolated_target_confirmation": True,
            "limitations": ["只读准备，不创建方案认可、执行写入或开放Source适配器。",
                            "网表映射要求Unique ID唯一且位号/Device一致，画布ID不等同网表键。",
                            "属性在导出中出现仅证明该字段被观察，不能证明全网表或DRC已刷新。"]}


def _read(path):
    path = external(Path(path))
    raw = path.read_bytes()
    return raw, json.loads(raw.decode("utf-8-sig"))


def _archive(kind, inputs, value):
    root = external(initialize() / "source-validations" / new_id(kind))
    root.mkdir(parents=True, exist_ok=False)
    refs = []
    for name, raw in inputs:
        with (root / name).open("xb") as stream:
            stream.write(raw)
        refs.append({"file": name, "sha256": hashlib.sha256(raw).hexdigest()})
    return write_new(root / ("case.json" if kind == "case" else "report.json"),
                     {"schema_version": 1, "kind": "source-validation-" + kind + "-archive", "created_at": now(),
                      "inputs": refs, kind: value})


def prepare_validation(source_path, audit_path, key, value):
    source_raw, source = _read(source_path)
    audit_raw, audit = _read(audit_path)
    case = make_case(source, audit, key, value)
    path = _archive("case", [("source-before.json", source_raw), ("audit-before.json", audit_raw)], case)
    return {"validation_path": str(path), "case_sha256": canonical_digest(case), "case": case}


def load_case(path):
    path = external(Path(path))
    raw_case, body = _read(path)
    if (not isinstance(body, dict) or body.get("kind") != "source-validation-case-archive" or
            type(body.get("schema_version")) is not int or body["schema_version"] != 1):
        raise ValueError("需要已归档的Source属性验证方案")
    refs = body.get("inputs")
    if not isinstance(refs, list) or len(refs) != 2:
        raise ValueError("验证方案缺少原始基线")
    raws, values = [], []
    for ref, name in zip(refs, ("source-before.json", "audit-before.json")):
        if not isinstance(ref, dict) or ref.get("file") != name:
            raise ValueError("验证方案归档文件名无效")
        raw, value = _read(path.parent / name)
        if hashlib.sha256(raw).hexdigest() != ref.get("sha256"):
            raise ValueError("验证方案基线字节摘要不一致")
        raws.append((name, raw))
        values.append(value)
    stored = body.get("case")
    parameters = stored.get("parameters") if isinstance(stored, dict) else None
    patch = parameters.get("patch") if isinstance(parameters, dict) else None
    if not isinstance(patch, dict) or len(patch) != 1:
        raise ValueError("验证方案只允许一个已有属性")
    key, value = next(iter(patch.items()))
    case = make_case(*values, key, value)
    if case != stored:
        raise ValueError("验证方案与原始基线重算结果不一致")
    return case, values, [("case.json", raw_case), *raws]


def check_validation(case_path, after_source_path, after_audit_path, save=False):
    case, baseline, archived = load_case(case_path)
    source_raw, source_after = _read(after_source_path)
    audit_raw, audit_after = _read(after_audit_path)
    before = _pair(*baseline)
    after = _pair(source_after, audit_after)
    params = case["parameters"]
    if (after[0]["primitive_id"] != params["primitive_id"] or after[0]["target"] != params["source_target"] or
            after[1]["identity"] != before[1]["identity"] or after[1]["window_id"] != before[1]["window_id"] or
            after[4] != before[4]):
        raise ValueError("前后验证证据的目标、窗口、名称或网表组件键变化")
    findings = []
    try:
        expected = {**before[2]["properties"], **params["patch"]}
        if ({k: v for k, v in before[2].items() if k != "properties"} !=
                {k: v for k, v in after[2].items() if k != "properties"} or after[2]["properties"] != expected):
            raise ValueError("Source详情尚未符合预期属性，或其他字段变化")
        verify_property_patch(before[1], after[1], params["primitive_id"], params["patch"])
        source_verified = True
    except ValueError as exc:
        source_verified = False
        findings.append(str(exc))
    key, expected_value = next(iter(params["patch"].items()))
    observed = after[5]["props"].get(key) == expected_value
    expected_netlist = json.loads(before[3]["raw_netlist"])
    expected_netlist["components"][before[4]]["props"][key] = expected_value
    only_property = expected_netlist == json.loads(after[3]["raw_netlist"])
    comparison = compare_audits(before[3], after[3])
    if not observed:
        findings.append("网表目标属性未出现预期新值，不能声明导出已观察到修改。")
    if not only_property:
        findings.append("完整网表尚未仅呈现预期单属性差异，需检查旧结果或其他变化。")
    if not comparison["no_observed_regressions"]:
        findings.append("连接或DRC存在变化/未匹配项，需逐项核查。")
    passed = source_verified and observed and only_property and comparison["no_observed_regressions"]
    report = {"schema_version": 1, "kind": "source-property-validation-report", "case_sha256": canonical_digest(case),
              "target": {"identity": case["identity"], "primitive_id": params["primitive_id"],
                         "netlist_component_id": case["netlist_component_id"], "patch": params["patch"]},
              "source_query_sha256": {"before": canonical_digest(baseline[0]), "after": canonical_digest(source_after)},
              "status": "property-observed-drc-freshness-unknown" if passed else "needs-investigation",
              "source_property_verified": source_verified, "netlist_property_observed": observed,
              "netlist_unchanged_except_property": only_property, "property_observation_passed": passed,
              "comparison": comparison, "findings": findings,
              "freshness_established": False, "drc_freshness_established": False,
              "verification_passed": False, "engineering_verified": False, "cad_write_authorized": False,
              "limitations": ["离线核对只能证明所给证据的属性/源/网表关系，不证明实际调用或刷新时序。",
                              "目标属性的新值不证明全网表连接或DRC与Source版本同步。",
                              "DRC新鲜度仍未知，不能开放写入或关闭工程验收。"]}
    if not save:
        return report
    path = _archive("report", [*archived, ("source-after.json", source_raw), ("audit-after.json", audit_raw)], report)
    return {"report_path": str(path), "report": report}


def check_roundtrip(case_path, trial_source_path, trial_audit_path, restored_source_path, restored_audit_path,
                    expected_drc_added=(), save=False):
    """Check a declared experimental stimulus and exact restoration independently.

An expected DRC warning can be a successful experimental observation while the
ordinary no-new-violations check remains failed. Neither grants normal writes.
"""
    if not isinstance(expected_drc_added, (list, tuple)) or any(
            not isinstance(item, dict) or item.get("type") not in {"warn", "error", "fatalError"} or
            not isinstance(item.get("rule"), str) or not item["rule"] or not item.get("primitives")
            for item in expected_drc_added):
        raise ValueError("实验预期DRC须逐项给出级别、规则与图元，不能使用汇总数量")
    case, baseline, archived = load_case(case_path)
    inputs = [*archived]
    values = []
    for path, name in zip((trial_source_path, trial_audit_path, restored_source_path, restored_audit_path),
                          ("trial-source.json", "trial-audit.json", "restored-source.json", "restored-audit.json")):
        raw, value = _read(path)
        inputs.append((name, raw))
        values.append(value)
    trial = check_validation(case_path, trial_source_path, trial_audit_path)
    before = _pair(*baseline)
    restored = _pair(*values[2:])
    if (restored[0]["target"] != before[0]["target"] or restored[0]["primitive_id"] != before[0]["primitive_id"] or
            restored[1]["identity"] != before[1]["identity"] or restored[1]["window_id"] != before[1]["window_id"] or
            restored[4] != before[4]):
        raise ValueError("恢复证据与实验目标不一致")
    drc = trial["comparison"]["drc"]
    matches = (sorted(map(canonical_digest, drc["added"])) == sorted(map(canonical_digest, expected_drc_added)) and
               not drc["removed"] and not drc["changed"] and not any(drc["unmatched"].values()))
    restored_checks = {"source_records": records(restored[1]["source"]) == records(before[1]["source"]),
                       "normalized_source_sha256": restored[3]["source_sha256"] == before[3]["source_sha256"],
                       "component_details": restored[2] == before[2],
                       "complete_netlist_bytes": restored[3]["netlist_sha256"] == before[3]["netlist_sha256"],
                       "detailed_drc": sorted(map(canonical_digest, restored[3]["drc"])) ==
                                       sorted(map(canonical_digest, before[3]["drc"]))}
    exact_restore = all(restored_checks.values())
    stimulus = (trial["source_property_verified"] and trial["netlist_property_observed"] and
                trial["netlist_unchanged_except_property"] and trial["comparison"]["connectivity"]["unchanged"])
    report = {"kind": "source-property-roundtrip-report", "case_sha256": canonical_digest(case),
              "target": trial["target"], "expected_drc_added": list(expected_drc_added),
              "trial_observation": trial, "stimulus_property_checks_passed": stimulus,
              "expected_drc_response_matched": matches, "restore_checks": restored_checks,
              "exact_restoration_observed": exact_restore,
              "experiment_expectations_met": bool(stimulus and matches and exact_restore),
              "local_rule_response_observed": bool(stimulus and matches and exact_restore and expected_drc_added),
              "drc_global_freshness_established": False, "verification_passed": False,
              "engineering_verified": False, "general_source_dispatch_enabled": False,
              "limitations": ["离线检查给定证据；预期刺激/恢复成功不改变原执行结果或普通DRC门禁。",
                              "局部规则出现/消失不是全部派生数据与Source版本同步的证明。"]}
    if not save:
        return report
    path = _archive("roundtrip", inputs, report)
    return {"report_path": str(path), "report": report}
