"""One-off restoration of a single observed ATTR zIndex to an archived baseline.

This investigator entry point cannot accept arbitrary source or normal edits.
setDocumentSource(string): Promise<boolean> is documented by SYS_FileManager.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

from .drawing_api import _IDENTITY
from .drawing_execution import _durable_new
from .drawing_source import records
from .project import object_digest
from .queries import _SOURCE_TARGET
from .source_trial import ACTIVE_CLAIM, lock_inventory
from .source_validation import _pair
from .storage import external, initialize, new_id, now, read_json
from .traceability import canonical_digest


def _same_export(left, right):
    return (left[3]["netlist_sha256"] == right[3]["netlist_sha256"] and
            sorted(map(canonical_digest, left[3]["drc"])) == sorted(map(canonical_digest, right[3]["drc"])) and
            left[2] == right[2])


def _make_restore(original_source, original_audit, current_source, current_audit):
    original, current = _pair(original_source, original_audit), _pair(current_source, current_audit)
    if (original[1]["identity"] != current[1]["identity"] or original[1]["window_id"] != current[1]["window_id"] or
            original[0]["target"] != current[0]["target"] or original[0]["primitive_id"] != current[0]["primitive_id"] or
            original[4] != current[4] or not _same_export(original, current)):
        raise ValueError("层级恢复要求原始与当前目标、器件详情、完整网表和详细DRC一致")
    old_rows, current_rows = records(original[1]["source"]), records(current[1]["source"])
    changed = [rid for rid in old_rows.keys() | current_rows.keys() if old_rows.get(rid) != current_rows.get(rid)]
    if len(changed) != 1 or changed[0] not in old_rows or changed[0] not in current_rows:
        raise ValueError("层级恢复仅允许一个已有属性记录的已知差异")
    rid = changed[0]
    old_env, old = old_rows[rid]
    env, current_value = current_rows[rid]
    if (env != old_env or env["type"] != "ATTR" or old.get("key") != "Name" or
            old.get("parentId") != original[0]["primitive_id"] or
            any(type(value.get("zIndex")) is not int for value in (old, current_value)) or
            {k: v for k, v in old.items() if k != "zIndex"} !=
            {k: v for k, v in current_value.items() if k != "zIndex"}):
        raise ValueError("仅可恢复Name属性的整数zIndex；其余字段和记录必须完全相同")
    lines = current[1]["source"].splitlines(keepends=True)
    for index, line in enumerate(lines):
        outer, sep, inner = line.partition("||")
        if sep and json.loads(outer).get("id") == rid:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines[index] = outer + sep + json.dumps(old, ensure_ascii=False, separators=(",", ":")) + "|" + ending
            break
    desired = "".join(lines)
    if records(desired) != old_rows:
        raise ValueError("恢复后源与完整原始基线不一致")
    case = {"kind": "single-name-z-index-restore", "identity": current[1]["identity"],
            "window_id": current[1]["window_id"], "source_target": current[0]["target"],
            "editor_version": current[0]["editor_version"], "primitive_id": current[0]["primitive_id"],
            "attribute_id": rid, "before": current_value["zIndex"], "after": old["zIndex"],
            "before_source_sha256": current[3]["source_sha256"],
            "desired_source_sha256": hashlib.sha256(desired.encode()).hexdigest()}
    return case, original, current, desired


def prepare_z_index_restore(original_source, original_audit, current_source, current_audit):
    paths = [external(Path(p)) for p in (original_source, original_audit, current_source, current_audit)]
    raw = [path.read_bytes() for path in paths]
    case, _, _, _ = _make_restore(*(json.loads(item.decode("utf-8-sig")) for item in raw))
    folder = initialize() / "source-repairs" / new_id("z-index")
    names = ("original-source.json", "original-audit.json", "current-source.json", "current-audit.json")
    inputs = []
    for name, data in zip(names, raw):
        path = external(folder / name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        inputs.append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})
    path = _durable_new(folder / "case.json", {"case": case, "inputs": inputs})
    return {"case_path": str(path), "case_sha256": canonical_digest(case), "case": case}


def _script(case, current, desired):
    payload = {**case, "source": current[1]["source"], "desired": desired}
    return "const input=JSON.parse(" + json.dumps(json.dumps(payload, ensure_ascii=True)) + ");" + _IDENTITY + _SOURCE_TARGET + r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source)||
    eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version||
    !same(await sourceTarget(before.identity),input.source_target))throw new Error('层级恢复基线或版本变化');
 attempted=true;
 const ok=await eda.sys_FileManager.setDocumentSource(input.desired);
 if(ok!==true)throw new Error('源恢复没有确认，禁止重试');
 let count=0;
 for(let i=0;i<24;i++){
  const state=await stable(input.identity);
  if(!same(await sourceTarget(state.identity),input.source_target))throw new Error('层级恢复目标变化');
  count=comparable(state.source)===comparable(input.desired)?count+1:0;
  if(count>=3)return {status:'verified',attempted,stable_reads:count,...state};
  await new Promise(resolve=>setTimeout(resolve,250));
 }
 throw new Error('恢复源未稳定，禁止重试');
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,error:String(error).slice(0,2000)};}
"""


def execute_z_index_restore(case_path, shown_sha256, user_message, reference, bridge_factory,
                           reviewed_lock_sha256=None, pause=time.sleep):
    case_path = external(Path(case_path))
    folder = case_path.parent
    body = read_json(case_path)
    names = ("original-source.json", "original-audit.json", "current-source.json", "current-audit.json")
    if [item["file"] for item in body["inputs"]] != list(names):
        raise ValueError("恢复输入清单不符")
    for item in body["inputs"]:
        if hashlib.sha256((folder / item["file"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("恢复输入字节摘要不符")
    case, original, current, desired = _make_restore(*(read_json(folder / name) for name in names))
    sha = canonical_digest(case)
    if case != body["case"] or sha != shown_sha256 or not all(isinstance(s, str) and s.strip() for s in (user_message, reference)):
        raise ValueError("恢复方案摘要或授权记录不符")
    if (folder / "claim.json").exists():
        raise ValueError("恢复已领取，禁止重放")
    key = {k: case["identity"][k] for k in ("project_uuid", "document_uuid")}
    lock = initialize() / "drawing-locks" / object_digest(key)
    prior, prior_sha = lock_inventory(lock)
    if any(item["file"] == ACTIVE_CLAIM for item in prior) or (prior and prior_sha != reviewed_lock_sha256):
        raise ValueError("存在活动实验或历史锁未复核")
    bridge = bridge_factory()
    health = bridge.health()
    if type(health.get("pendingRequests")) is not int or health["pendingRequests"] != 0:
        raise ValueError("Bridge有待处理请求")
    live_source = bridge.design_query("source-component", case["window_id"], case["primitive_id"])
    live_audit = bridge.design_query("audit", case["window_id"])
    live = _pair(live_source, live_audit)
    if live[1]["identity"] != case["identity"] or live[1]["window_id"] != case["window_id"] or live[0]["target"] != case["source_target"] or live[0]["primitive_id"] != case["primitive_id"] or live[3]["source_sha256"] != current[3]["source_sha256"] or not _same_export(live, current):
        raise ValueError("层级恢复前现场已漂移")
    script = _script(case, live, desired)
    _durable_new(folder / "authorization.json", {"created_at": now(), "case_sha256": sha,
                 "user_message": user_message, "reference": reference, "prior_locks": prior})
    claim = {"run_dir": str(folder), "case_sha256": sha, "purpose": "single-name-z-index-restore", "created_at": now()}
    if lock_inventory(lock)[1] != prior_sha:
        raise ValueError("恢复准备时目标锁变化")
    _durable_new(lock / ACTIVE_CLAIM, claim)
    _durable_new(folder / "claim.json", claim)
    status, error, attempted = "blocked", None, False
    try:
        files, _ = lock_inventory(lock)
        if [item for item in files if item["file"] != ACTIVE_CLAIM] != prior:
            raise ValueError("历史锁已变化，禁止恢复写入")
        _durable_new(folder / "intent.json", {"case_sha256": sha, "script_sha256": hashlib.sha256(script.encode()).hexdigest()})
        status, attempted = "uncertain", True
        reply, window = bridge._execute_fixed(script, case["window_id"])
        _durable_new(folder / "reply.json", {"window_id": window, **reply})
        if window != case["window_id"]:
            raise ValueError("恢复返回了不同窗口，保留未知状态")
        if reply.get("status") == "blocked" and reply.get("attempted") is False:
            status, attempted = "blocked", False
        if (reply.get("status") != "verified" or reply.get("attempted") is not True or
                type(reply.get("stable_reads")) is not int or reply["stable_reads"] < 3 or
                reply.get("identity") != case["identity"] or records(reply["source"]) != records(original[1]["source"])):
            raise ValueError("层级恢复未通过完整源回读")
        for sample in range(6):
            if sample:
                pause(1)
            source = bridge.design_query("source-component", case["window_id"], case["primitive_id"])
            audit = bridge.design_query("audit", case["window_id"])
            _durable_new(folder / f"{sample+1:03d}-source.json", source)
            _durable_new(folder / f"{sample+1:03d}-audit.json", audit)
            after = _pair(source, audit)
            if (after[1]["identity"] == case["identity"] and after[1]["window_id"] == case["window_id"] and
                    after[0]["target"] == case["source_target"] and after[0]["primitive_id"] == case["primitive_id"] and
                    records(after[1]["source"]) == records(original[1]["source"]) and _same_export(after, original)):
                status = "restored_observed"
                break
        if status != "restored_observed":
            raise ValueError("层级恢复后导出尚未匹配原始基线")
    except Exception as exc:
        error = str(exc)
    result = {"kind": "single-name-z-index-restore-result", "case_sha256": sha, "status": status,
              "attempted": attempted, "error": error, "run_dir": str(folder), "target_lock": str(lock),
              "general_source_dispatch_enabled": False, "drc_freshness_established": False, "finished_at": now()}
    _durable_new(folder / "result.json", result)
    if status in {"blocked", "restored_observed"}:
        if read_json(lock / ACTIVE_CLAIM) != claim:
            raise ValueError("实验锁持有人变化")
        (lock / ACTIVE_CLAIM).unlink()
    return result
