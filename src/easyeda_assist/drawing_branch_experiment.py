"""Independent cleanup of an observed three-step branch experiment prefix.

No normal dispatch or arbitrary desired source. A reviewed prefix is checked
against its original receipts and live full audit before archived restoration.
"""
from copy import deepcopy
from pathlib import Path

from .drawing_api import comparable_source
from .drawing_branch import verify_branch_reply, audit_branch
from .drawing_execution import _durable_new
from .drawing_incremental import verify_lock, _same_state, load_baseline
from .drawing_incremental_cleanup import _restore_archived_baseline
from .drawing_circuit import _text
from .design_audit import unwrap_audit
from .operations import BRANCH
from .project import object_digest
from .storage import external, read_json, now


def review_prefix(run_dir):
    """A first placement and one wire were observed; the second never started."""
    run_dir=external(Path(run_dir))
    result,plan=read_json(run_dir/'result.json'),read_json(run_dir/'plan.json')
    claim=read_json(run_dir/'claim.json')
    if (result.get('status')!='uncertain' or result.get('completed')!=1 or result.get('total')!=3 or
            plan.get('execution_contract')!=BRANCH or len(plan['operations'])!=3 or
            [op['operation'] for op in plan['operations']]!=['place_component','add_wire','add_wire'] or
            any((run_dir/f'{i:03d}-intent.json').exists() for i in range(3,51)) or
            claim.get('plan_sha256')!=object_digest({k:v for k,v in plan.items() if k!='approval'}) or
            run_dir.name!=claim['plan_sha256'] or claim.get('adapter')!=BRANCH):
        raise ValueError('仅可独立复核首步器件及第一条支路均有完整回包的失败三步实验')
    original=read_json(run_dir/'input-snapshot.json')
    baseline,_,_=load_baseline(plan,original)
    if baseline!=read_json(run_dir/'incremental-baseline.json'):
        raise ValueError('实验归档基线与绑定文件不一致')
    state,mapping=original,{}
    for index,op in enumerate(plan['operations'][:2],1):
        receipt=read_json(run_dir/f'{index:03d}-reply.json')
        intent=read_json(run_dir/f'{index:03d}-intent.json')
        if intent.get('operation')!=op or intent.get('target')!=original['identity'] or intent.get('source_sha256')!=object_digest(state['source']):
            raise ValueError('实验意图或输入源不匹配')
        state=verify_branch_reply(receipt,state,op,plan)
        mapping[op['id']]=receipt['primitive_id']
    prefix=deepcopy(plan)
    pending=prefix['operations'].pop()
    prefix['branch_anchors']=[a for a in prefix['branch_anchors'] if a['operation']!=pending['id']]
    # A pin reached only by the unattempted wire must still be unconnected.
    reached={op['parameters']['net'] for op in prefix['operations'] if op['operation']=='add_wire'}
    for pin in prefix['connectivity']:
        if pin['net'] not in reached:pin['net']=''
    return original,baseline,state,prefix,mapping


def restore_branch_prefix(run_dir,bridge,user_message,reference):
    """One explicit archived restore; no replay or automatic failed-run cleanup."""
    if not _text(user_message) or not _text(reference):
        raise ValueError('须记录真实实验委托与本次恢复依据')
    run_dir=external(Path(run_dir))
    original,baseline,state,prefix,mapping=review_prefix(run_dir)
    result=read_json(run_dir/'result.json');review=read_json(run_dir/'incremental-lock-review.json')
    lock,owned,prior=Path(result['target_lock']),review['claim'],review['prior']
    verify_lock(lock,owned,prior)
    if Path(owned['run_dir'])!=run_dir or bridge.health().get('pendingRequests')!=0:
        raise ValueError('恢复目标锁或待处理请求不符')
    before=bridge.drawing_snapshot(original['window_id'])
    if (before['window_id']!=original['window_id'] or before['identity']!=original['identity'] or
            comparable_source(before['source'])!=comparable_source(state['source'])):
        raise ValueError('现场不精确匹配已复核前缀，禁止恢复')
    live=unwrap_audit(bridge.design_query('audit',original['window_id']))
    _same_state(before,live)
    audit=audit_branch(live,before,prefix,baseline,mapping)
    delta=audit['drc_delta']
    if (not audit['connectivity_matches'] or delta['removed'] or delta['changed'] or any(delta['unmatched'].values())):
        raise ValueError('原电路、支路前缀或详细DRC存在未解释变化')
    part=mapping[prefix['operations'][0]['id']]
    wire=mapping[prefix['operations'][1]['id']]
    for warning in delta['added']:
        primitives=warning.get('primitives',[])
        allowed=(warning.get('rule')=='CHECKFLOATPIN' and len(primitives)==1 and
                 primitives[0].get('primitiveId','').startswith(part+'-')) or (
                 warning.get('rule')=='ONENETNAME' and len(primitives)==1 and primitives[0].get('primitiveId')==wire)
        if not allowed:
            raise ValueError('存在实验新器件浮空或重复网名之外的新增DRC')
    _durable_new(run_dir/'explicit-branch-prefix-review.json',{'created_at':now(),'user_message':user_message,
        'reference':reference,'review':audit,'mapping':mapping,'original_result_preserved':True})
    return _restore_archived_baseline(run_dir,bridge,user_message,reference,original,
        unwrap_audit(baseline['audit']),before,live,lock,owned,prior)
