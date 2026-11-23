"""Explicit experiment cleanup outside the normal incremental dispatcher.

Only a known isolated first creation or an archived failed experiment can be
removed. Original records and the full exported circuit must remain intact;
one write is followed by independent verification. No automatic recovery.
"""
from pathlib import Path

from .design_audit import unwrap_audit
from .drawing_circuit import _text
from .drawing_source import records
from .operations import INCREMENTAL, BRANCH
from .storage import external, read_json
from .drawing_incremental import ACTIVE_CLAIM, _same_state, _components, verify_lock, release_lock


def cleanup_failed_creation(run_dir, bridge, user_message, reference):
    """Explicit separate cleanup of a known, isolated first-step test creation.

    Never used by execute_plan. The failed result and historical locks survive.
    A completed step, extra object, connected probe or altered original blocks.
    """
    import json
    from .drawing_api import _IDENTITY
    from .drawing_source import verify_addition
    from .drawing_execution import _durable_new
    from .traceability import canonical_digest
    run_dir = external(Path(run_dir))
    result, plan = read_json(run_dir/"result.json"), read_json(run_dir/"plan.json")
    original = read_json(run_dir/"input-snapshot.json")
    first = read_json(run_dir/"001-reply.json")
    pid = first.get("primitive_id")
    if (result.get("status") != "uncertain" or result.get("completed") != 0 or
            plan.get("execution_contract") not in {INCREMENTAL, BRANCH} or
            plan["operations"][0]["operation"] != "place_component" or not _text(pid) or
            any((run_dir/f"{i:03d}-intent.json").exists() for i in range(2,51)) or
            not _text(user_message) or not _text(reference)):
        raise ValueError("仅可清理已完整复核的首步孤立实验器件")
    folder = run_dir/"explicit-probe-cleanup"
    review = read_json(run_dir/"incremental-lock-review.json")
    lock,owned,prior = Path(result["target_lock"]),review["claim"],review["prior"]
    verify_lock(lock,owned,prior)
    if bridge.health().get("pendingRequests") != 0:
        raise ValueError("清理前仍有待处理请求")
    before = bridge.drawing_snapshot(original["window_id"])
    if before["identity"] != original["identity"] or before["window_id"] != original["window_id"]:
        raise ValueError("清理目标身份不符")
    verify_addition(original["source"],before["source"],pid,"COMPONENT")
    live = unwrap_audit(bridge.design_query("audit",original["window_id"]))
    _same_state(before,live)
    baseline = unwrap_audit(read_json(run_dir/"incremental-baseline.json")["audit"])
    old,new = _components(baseline["raw_netlist"]),_components(live["raw_netlist"])
    attrs = {v.get("key"):v.get("value") for e,v in records(before["source"]).values()
             if e["type"] == "ATTR" and v.get("parentId") == pid}
    uid = attrs.get("Unique ID")
    if (not _text(uid) or set(new)-set(old) != {uid} or any(new.get(k)!=v for k,v in old.items()) or
            any(pin["net"] for pin in new[uid]["pinInfoMap"].values())):
        raise ValueError("清理器件已连接或原电路网表变化")
    from .source_trial import lock_inventory
    lock = Path(result["target_lock"])
    owned = read_json(lock/ACTIVE_CLAIM)
    if Path(owned["run_dir"]) != run_dir:
        raise ValueError("清理对象与当前实验锁不符")
    prior = read_json(run_dir/"incremental-lock-review.json")["prior"]
    verify_lock(lock,owned,prior)
    payload = {"identity":before["identity"],"source":before["source"],"primitive_id":pid,
               "unique_id":uid,"device":attrs.get("Device"),"designator":attrs.get("Designator")}
    literal = json.dumps(json.dumps(payload))
    script = "const input=JSON.parse(" + literal + ");" + _IDENTITY + r"""
let attempted=false;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source)||eda.sys_Environment.getEditorCurrentVersion()!=='4.1.100')throw new Error('清理基线漂移');
 const item=await eda.sch_PrimitiveComponent.get(input.primitive_id);
 if(!item||item.getState_PrimitiveId()!==input.primitive_id||item.getState_UniqueId()!==input.unique_id||
  item.getState_Designator()!==input.designator||item.getState_Component()?.uuid!==input.device)throw new Error('清理对象身份不符');
 if(!same(await identity(),input.identity))throw new Error('清理前目标变化');
 attempted=true;
 if(await eda.sch_PrimitiveComponent.delete(input.primitive_id)!==true)throw new Error('清理返回失败');
 const after=await stable(input.identity);
 return {status:'removed',attempted,...after};
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,error:String(error)};}
"""
    folder.mkdir()
    _durable_new(folder/"plan.json",{"user_message":user_message,"reference":reference,"primitive_id":pid,
        "before":before,"audit":live,"expected":"Remove only this isolated failed test creation; restore initial complete source/netlist/DRC."})
    _durable_new(folder/"intent.json",{"script_sha256":canonical_digest(script)})
    verify_lock(lock,owned,prior)
    reply,window = bridge._execute_fixed(script,original["window_id"])
    _durable_new(folder/"reply.json",{**reply,"window_id":window})
    if window != original["window_id"] or reply.get("status") != "removed":
        raise ValueError("清理结果未知，保留活动锁")
    after = bridge.drawing_snapshot(original["window_id"])
    audit = unwrap_audit(bridge.design_query("audit",original["window_id"]))
    _durable_new(folder/"after-source.json",after)
    _durable_new(folder/"after-audit.json",audit)
    _same_state(after,audit)
    if (records(after["source"]) != records(original["source"]) or
            audit["raw_netlist"] != baseline["raw_netlist"] or audit["drc"] != baseline["drc"]):
        raise ValueError("清理后完整原始基线不一致，保留活动锁")
    _durable_new(folder/"result.json",{"status":"original-baseline-restored","primitive_id":pid,
                    "original_failed_result_preserved":True,"historical_locks_preserved":True})
    release_lock(lock,owned,prior)
    return {"status":"original-baseline-restored","cleanup_dir":str(folder)}



def restore_experiment_baseline(run_dir,bridge,user_message,reference):
    """Explicit whole-experiment removal, only if every original record survives.

    The desired source comes exclusively from the archived input, never caller
    text. Known remaining additions/orphans must match the recorded experiment.
    This investigator path is not registered as a drawing operation.
    """
    import json,time
    from .bridge import BridgeError
    from .drawing_api import _IDENTITY
    from .drawing_execution import _durable_new
    from .traceability import canonical_digest
    run_dir=external(Path(run_dir))
    result,plan=read_json(run_dir/"result.json"),read_json(run_dir/"plan.json")
    if (result.get("status") != "verification_failed" or plan.get("execution_contract") != INCREMENTAL or
            result.get("completed") != len(plan["operations"]) or not _text(user_message) or not _text(reference)):
        raise ValueError("仅可显式恢复全部操作已观察的失败实验基线")
    original=read_json(run_dir/"input-snapshot.json")
    final=read_json(run_dir/f"{len(plan['operations']):03d}-snapshot.json")
    baseline=unwrap_audit(read_json(run_dir/"incremental-baseline.json")["audit"])
    review=read_json(run_dir/"incremental-lock-review.json")
    lock,owned,prior=Path(result["target_lock"]),review["claim"],review["prior"]
    verify_lock(lock,owned,prior)
    if Path(owned["run_dir"]) != run_dir or bridge.health().get("pendingRequests") != 0:
        raise ValueError("恢复目标或待处理请求不符")
    before=bridge.drawing_snapshot(original["window_id"])
    old,known,current=records(original["source"]),records(final["source"]),records(before["source"])
    if (before["window_id"] != original["window_id"] or before["identity"] != original["identity"] or
            not set(old) <= set(current) <= set(known) or any(current.get(rid)!=row for rid,row in old.items())):
        raise ValueError("原电路或实验记录集合变化，禁止源恢复")
    for rid in current.keys()-old.keys():
        env,value=known[rid];curenv,curvalue=current[rid]
        layout=(env["type"]=="ATTR" and value.get("key") in {"NET","Relevance"} and
                value.get("zIndex") is None and curvalue.get("zIndex") in (0,2))
        if ({k:v for k,v in env.items() if k!="ticket"}!={k:v for k,v in curenv.items() if k!="ticket"} or
                type(curenv.get("ticket")) is not int or not 0<=curenv["ticket"]<=env.get("ticket",-1) or
                {k:v for k,v in value.items() if k!="zIndex" or not layout} !=
                {k:v for k,v in curvalue.items() if k!="zIndex" or not layout}):
            raise ValueError("剩余实验对象内容改变，禁止源恢复")
    live=unwrap_audit(bridge.design_query("audit",original["window_id"]))
    _same_state(before,live)
    oldnet,newnet=_components(baseline["raw_netlist"]),_components(live["raw_netlist"])
    if any(newnet.get(uid)!=component for uid,component in oldnet.items()):
        raise ValueError("原电路网表变化，禁止源恢复")
    return _restore_archived_baseline(run_dir,bridge,user_message,reference,original,baseline,before,live,lock,owned,prior)


def _restore_archived_baseline(run_dir,bridge,user_message,reference,original,baseline,before,live,lock,owned,prior):
    """Internal one-write mechanism; callers must prove their specific delta."""
    import json,time
    from .bridge import BridgeError
    from .drawing_api import _IDENTITY
    from .drawing_execution import _durable_new
    from .traceability import canonical_digest
    old,current=records(original["source"]),records(before["source"])
    folder=run_dir/"explicit-baseline-source-restore"
    folder.mkdir()
    _durable_new(folder/"plan.json",{"user_message":user_message,"reference":reference,"before":before,
        "audit":live,"desired":original,"removed_record_ids":sorted(current.keys()-old.keys())})
    payload={"identity":original["identity"],"source":before["source"],"desired":original["source"]}
    script="const input=JSON.parse("+json.dumps(json.dumps(payload))+");"+_IDENTITY+r"""
let attempted=false;
try{
 const state=await stable(input.identity);
 if(comparable(state.source)!==comparable(input.source)||eda.sys_Environment.getEditorCurrentVersion()!=='4.1.100')throw new Error('恢复基线或版本变化');
 if(!same(await identity(),input.identity))throw new Error('恢复前目标变化');
 attempted=true;
 const ok=await eda.sys_FileManager.setDocumentSource(input.desired);
 return {status:ok===true?'restoration_pending':'uncertain',attempted,ok};
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,error:String(error)};}
"""
    _durable_new(folder/"intent.json",{"script_sha256":canonical_digest(script)})
    verify_lock(lock,owned,prior)
    reply,window=bridge._execute_fixed(script,original["window_id"])
    _durable_new(folder/"reply.json",{**reply,"window_id":window})
    if window!=original["window_id"] or reply.get("status")!="restoration_pending":
        raise ValueError("源恢复结果未知，保留活动锁")
    equal=0
    for sample in range(12):
        if sample:time.sleep(0.5)
        try:
            after=bridge.drawing_snapshot(original["window_id"])
            audit=unwrap_audit(bridge.design_query("audit",original["window_id"]))
            _durable_new(folder/f"{sample:03d}-observation.json",{"source":after,"audit":audit})
            _same_state(after,audit)
            matched=(after["identity"]==original["identity"] and after["window_id"]==original["window_id"] and
                records(after["source"])==old and audit["raw_netlist"]==baseline["raw_netlist"] and
                sorted(map(canonical_digest,audit["drc"]))==sorted(map(canonical_digest,baseline["drc"])))
            equal=equal+1 if matched else 0
            if equal>=3:
                _durable_new(folder/"result.json",{"status":"original-baseline-restored","stable_observations":equal,
                    "original_failed_result_preserved":True,"historical_locks_preserved":True})
                release_lock(lock,owned,prior)
                return {"status":"original-baseline-restored","cleanup_dir":str(folder)}
        except BridgeError as exc:
            _durable_new(folder/f"{sample:03d}-read-error.json",{"error":str(exc)})
            equal=0
    raise ValueError("源恢复后完整基线尚未确认，保留活动锁")
