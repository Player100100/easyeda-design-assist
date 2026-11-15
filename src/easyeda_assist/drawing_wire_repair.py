"""Bounded replacement of explicitly journalled generated circuit wires.

Used to recover editor merges during layout repair. Callers preserve before
snapshots and inspect each reply. Does not operate on components or source text.
"""
import json
from .drawing_api import _IDENTITY, validate_snapshot
from .drawing_source import records


def script(snapshot, ids=None, line=None, net=None):
    validate_snapshot(snapshot)
    rows=records(snapshot['source'])
    if ids is not None:
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or len(set(ids))!=len(ids) or any(i not in rows or rows[i][0]['type']!='WIRE' for i in ids):
            raise ValueError('修复删除仅接受快照中明确列出的导线')
        payload={'ids':ids,'action':'remove'}
    else:
        if net not in ('GND','24V_IN','5V_OUT','SW_DCDC') or not isinstance(line,list) or not line or len(line)>30:
            raise ValueError('修复创建仅接受DC/DC网络与明确线段')
        for segment in line:
            if not isinstance(segment,list) or len(segment)!=4 or any(type(v) not in (int,float) or not 0<=v<=1200 for v in segment):raise ValueError('修复线段越界')
        payload={'line':line,'net':net,'action':'create'}
    payload.update(identity=snapshot['identity'],source=snapshot['source'])
    return 'const input=JSON.parse('+json.dumps(json.dumps(payload,ensure_ascii=True))+');'+_IDENTITY+r"""
const before=await stable(input.identity);if(comparable(before.source)!==comparable(input.source))throw new Error('导线修复前文档变化');
let primitiveId=null;
if(input.action==='remove'){
 if(!await eda.sch_PrimitiveWire.delete(input.ids))throw new Error('删除返回失败，须独立回读');
}else{
 const item=await eda.sch_PrimitiveWire.create(input.line,input.net);
 if(!item)throw new Error('导线创建无返回');primitiveId=item.getState_PrimitiveId();
}
const after=await stable(input.identity);return {primitive_id:primitiveId,...after};
"""
