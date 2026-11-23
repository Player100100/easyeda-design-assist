"""Read-only audit evidence and conservative netlist/DRC differences.

Source stability does not prove that asynchronous derived models are fresh.
This format deliberately cannot establish write verification by itself.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .drawing_api import comparable_source, validate_snapshot
from .schematic import components_from_raw
from .storage import external, initialize, new_id, now, write_new
from .traceability import canonical_digest

DRC_SETTINGS = {"strict": True, "user_interface": False, "verbose": True}
SCOPE = {"netlist": "single-api-export", "drc": "current-active-schematic-api-result"}
FRESHNESS = {"source": "checked-before-after", "derived": "not-established"}


def source_digest(source):
    return hashlib.sha256(comparable_source(source).encode("utf-8")).hexdigest()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_audit(snapshot, reply, query_sha256):
    validate_snapshot(snapshot)
    if not isinstance(reply, dict) or reply.get("identity") != snapshot["identity"] or reply.get("window_id") != snapshot["window_id"]:
        raise ValueError("审计目标或窗口与输入快照不一致")
    observed = {**snapshot, "source": reply.get("source")}
    validate_snapshot(observed)
    if source_digest(observed["source"]) != source_digest(snapshot["source"]):
        raise ValueError("审计期间完整源发生变化")
    components_from_raw(reply.get("raw_netlist"))
    record = {"schema_version": 1, "kind": "design-audit", "captured_at": now(),
              "domain": "schematic", "identity": deepcopy(reply["identity"]), "window_id": reply["window_id"],
              "editor_version": reply.get("editor_version"), "source": reply["source"],
              "source_sha256": source_digest(reply["source"]), "raw_netlist": reply.get("raw_netlist"),
              "netlist_sha256": _sha(reply.get("raw_netlist", "").encode("utf-8")),
              "drc": deepcopy(reply.get("drc")), "drc_settings": deepcopy(DRC_SETTINGS),
              "scope": deepcopy(SCOPE), "freshness": deepcopy(FRESHNESS),
              "provenance": {"transport": "bridge", "method": "SCH_ManufactureData.getNetlistFile / SCH_Drc.check",
                             "query_sha256": query_sha256},
              "limitations": ["源稳定不证明派生网表/DRC已反映本次写入；新鲜度保留未知。",
                              "网表范围以本次API导出为准，不推断全工程或跨板覆盖。",
                              "详细DRC不兼容或缺少稳定身份时保留未匹配项，不按数量判定通过。"]}
    validate_audit(record)
    return record


def validate_audit(record):
    if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != 1 or record.get("kind") != "design-audit":
        raise ValueError("需要design-audit审计记录")
    validate_snapshot({**record, "kind": "drawing-source"})
    if not isinstance(record.get("editor_version"), str) or not record["editor_version"].strip():
        raise ValueError("审计记录缺少编辑器版本，须重新采集基线")
    if record.get("source_sha256") != source_digest(record["source"]):
        raise ValueError("审计完整源摘要不一致")
    components_from_raw(record.get("raw_netlist"))
    if record.get("netlist_sha256") != _sha(record["raw_netlist"].encode("utf-8")):
        raise ValueError("审计网表摘要不一致")
    if record.get("freshness") != FRESHNESS:
        raise ValueError("当前审计契约不能声明派生读模型已与Source版本绑定")
    if record.get("scope") != SCOPE or record.get("drc_settings") != DRC_SETTINGS:
        raise ValueError("审计范围或DRC设置不符合当前契约，须重新采集基线")
    errors = record.get("drc")
    if not isinstance(errors, list) or len(errors) > 5000 or any(
        not isinstance(error, dict) or error.get("type") not in {"fatalError", "error", "warn"} for error in errors
    ):
        raise ValueError("DRC必须是详细结果数组，不能使用布尔值或汇总数量")
    canonical_digest(record)  # Reject nonfinite numbers and unserializable evidence.


def unwrap_audit(value):
    if isinstance(value, dict) and value.get("kind") == "design-query":
        data = value.get("data")
        validate_audit(data)
        target, provenance = value.get("target"), value.get("provenance")
        if (value.get("query") != "audit" or not isinstance(target, dict) or not isinstance(provenance, dict) or
                target.get("identity") != data["identity"] or target.get("window_id") != data["window_id"] or
                provenance.get("editor_version") != data["editor_version"]):
            raise ValueError("统一查询外壳与审计记录不一致")
        value = data
    validate_audit(value)
    return value


def _connections(record):
    components = components_from_raw(record["raw_netlist"])
    pins, names = {}, {}
    for cid, component in components.items():
        names[cid] = component["props"].get("Designator")
        for pin in component["pinInfoMap"].values():
            number = pin["number"]
            if not number.strip() or (cid, number) in pins:
                raise ValueError("网表引脚编号缺失或重复，无法可靠比较")
            pins[cid, number] = pin["net"]
    return pins, names


def _connectivity_delta(before, after):
    old, old_names = _connections(before)
    new, new_names = _connections(after)
    def entry(key, net):
        return {"component_id": key[0], "pin": key[1], "net": net}
    delta = {"added_components": sorted(new_names.keys() - old_names.keys()),
             "removed_components": sorted(old_names.keys() - new_names.keys()),
             "renamed_components": [{"component_id": cid, "before": old_names[cid], "after": new_names[cid]}
                                    for cid in sorted(old_names.keys() & new_names.keys()) if old_names[cid] != new_names[cid]],
             "added_pins": [entry(key, new[key]) for key in sorted(new.keys() - old.keys())],
             "removed_pins": [entry(key, old[key]) for key in sorted(old.keys() - new.keys())],
             "changed_pins": [{"component_id": key[0], "pin": key[1], "before": old[key], "after": new[key]}
                              for key in sorted(old.keys() & new.keys()) if old[key] != new[key]]}
    delta["unchanged"] = not any(delta.values())
    return delta


def _drc_anchor(error):
    rule, primitives = error.get("rule"), error.get("primitives")
    if not isinstance(rule, str) or not rule.strip() or not isinstance(primitives, list) or not primitives:
        return None
    identities = []
    for item in primitives:
        if not isinstance(item, dict) or not isinstance(item.get("primitiveId"), str) or not item["primitiveId"].strip():
            return None
        if not isinstance(item.get("sheet"), str) or not item["sheet"].strip():
            return None
        identities.append((item.get("sheet"), item["primitiveId"]))
    if len(set(identities)) != len(identities):
        return None
    return canonical_digest({"rule": rule, "primitives": sorted(identities, key=lambda x: (x[0] or "", x[1]))})


def _drc_fingerprint(error):
    normalized = {**error, "primitives": sorted(error["primitives"], key=canonical_digest)}
    return canonical_digest(normalized)


def _drc_delta(before, after):
    groups = [defaultdict(list), defaultdict(list)]
    unresolved = {"before": [], "after": []}
    for side, errors, grouped in zip(("before", "after"), (before, after), groups):
        for index, error in enumerate(errors):
            anchor = _drc_anchor(error)
            if anchor is None:
                unresolved[side].append({"index": index, "raw": error, "reason": "缺少可匹配的规则/图元/页身份"})
            else:
                grouped[anchor].append((_drc_fingerprint(error), error))
    added, removed, changed, unchanged = [], [], [], []
    for anchor in sorted(groups[0].keys() | groups[1].keys()):
        left = sorted(groups[0][anchor], key=lambda item: item[0])
        right_by_fingerprint = defaultdict(deque)
        for fingerprint, error in groups[1][anchor]:
            right_by_fingerprint[fingerprint].append(error)
        remaining = []
        for fingerprint, error in left:
            candidates = right_by_fingerprint.get(fingerprint)
            if not candidates:
                remaining.append(error)
            else:
                unchanged.append(error)
                candidates.popleft()
        right = [error for fingerprint in sorted(right_by_fingerprint)
                 for error in right_by_fingerprint[fingerprint]]
        if len(remaining) > 1 or len(right) > 1:
            for side, errors in (("before", remaining), ("after", right)):
                unresolved[side].extend({"raw": error, "reason": "同一规则/图元身份有多个候选，无法唯一匹配"} for error in errors)
        elif remaining and right:
            changed.append({"before": remaining[0], "after": right[0]})
        else:
            removed.extend(remaining)
            added.extend(right)
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged,
            "unmatched": unresolved,
            "no_new_violations": not added and not changed and not any(unresolved.values())}


def compare_audits(before, after):
    before, after = unwrap_audit(before), unwrap_audit(after)
    identity_keys = ("project_uuid", "document_uuid", "document_type")
    if any(before["identity"][key] != after["identity"][key] for key in identity_keys):
        raise ValueError("不能比较不同工程或图页的审计记录")
    compatible = before["editor_version"] == after["editor_version"]
    connections = _connectivity_delta(before, after) if compatible else None
    drc = _drc_delta(before["drc"], after["drc"]) if compatible else None
    matches = bool(compatible and connections["unchanged"] and drc["no_new_violations"])
    return {"schema_version": 1, "kind": "design-audit-comparison", "comparable": compatible,
            "target": {key: before["identity"][key] for key in identity_keys},
            "before": {"audit_sha256": canonical_digest(before), "source_sha256": before["source_sha256"],
                       "editor_version": before["editor_version"]},
            "after": {"audit_sha256": canonical_digest(after), "source_sha256": after["source_sha256"],
                      "editor_version": after["editor_version"]},
            "source_changed": before["source_sha256"] != after["source_sha256"],
            "connectivity": connections, "drc": drc, "no_observed_regressions": matches,
            "freshness_established": False, "verification_passed": False,
            "findings": ([] if compatible else ["编辑器版本不同，须重新建立同版本基线。"])
                        + ["派生网表/DRC尚无与Source版本绑定的新鲜度证据；观察一致也不能声明写后核验通过。"],
            "engineering_verified": False}


def compare_files(before_path: Path, after_path: Path, save=False):
    paths = [external(before_path), external(after_path)]
    raws = [path.read_bytes() for path in paths]
    values = [json.loads(raw.decode("utf-8-sig")) for raw in raws]
    comparison = compare_audits(*values)
    if not save:
        return comparison
    root = external(initialize() / "audit-comparisons" / new_id("comparison"))
    root.mkdir(parents=True, exist_ok=False)
    inputs = []
    for name, path, raw in zip(("before.json", "after.json"), paths, raws):
        with (root / name).open("xb") as stream:
            stream.write(raw)
        inputs.append({"file": name, "source_path": str(path), "sha256": _sha(raw)})
    path = write_new(root / "report.json", {"schema_version": 1, "kind": "audit-comparison-archive",
                     "created_at": now(), "inputs": inputs, "comparison": comparison})
    return {"comparison_path": str(path), "comparison": comparison}


def load_comparison(path: Path):
    path = external(path)
    raw_report = path.read_bytes()
    record = json.loads(raw_report.decode("utf-8-sig"))
    if not isinstance(record, dict) or record.get("kind") != "audit-comparison-archive" or record.get("schema_version") != 1:
        raise ValueError("需要已归档的审计比较报告")
    inputs = record.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != 2:
        raise ValueError("审计比较缺少两个原始输入")
    values = []
    for item, name in zip(inputs, ("before.json", "after.json")):
        if not isinstance(item, dict) or item.get("file") != name:
            raise ValueError("审计比较归档文件名无效")
        raw = external(path.parent / name).read_bytes()
        if _sha(raw) != item.get("sha256"):
            raise ValueError("审计比较归档输入摘要不一致")
        values.append(json.loads(raw.decode("utf-8-sig")))
    comparison = compare_audits(*values)
    if comparison != record.get("comparison"):
        raise ValueError("审计比较结论与原始输入不一致")
    return comparison, {"path": str(path), "sha256": _sha(raw_report)}
