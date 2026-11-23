"""Fixed PCB API programs, checked against easyeda-api-skill 1.1.29.

No arbitrary scripts, source replacement or order APIs. Source-bound deletion
is limited to explicit test-board repair helpers. These programs remain
experimental outside their recorded test-board scope.
"""
from __future__ import annotations

import json


LAYER_NAMES = ("TOP", "BOTTOM", "BOARD_OUTLINE", "MULTI") + tuple(
    f"INNER_{i}" for i in range(1, 31))
# API Gateway runs without SDK enum globals on 4.1.100. Named projections
# below are checked against skill references/enums, not inferred from a layer ID.
GATEWAY_ENUMS = {"LayerId": {"TOP": 1, "BOTTOM": 2, "BOARD_OUTLINE": 11, "MULTI": 12, "INNER_1":15,"INNER_2":16},
                 "ViaType": {"VIA": 0}, "PourFillMethod": {"SOLID": "solid"},
                 "RoutingExistingPrimitiveMode": {"KEEP": "keep"},
                 "RoutingOptimization": {"FASTER": 0, "COMPLETION": 1}}
COMMON = ["PrimitiveId", "PrimitiveType", "PrimitiveLock"]
FIELDS = {
    "Component": COMMON + ["X", "Y", "Rotation", "Layer", "Designator", "UniqueId",
        "Component", "Footprint", "Name", "OtherProperty", "Pads"],
    "Pad": COMMON + ["Net", "PadNumber", "X", "Y", "Rotation", "Layer", "Pad",
        "Hole", "HoleOffsetX", "HoleOffsetY", "HoleRotation", "Metallization", "PadType",
        "SpecialPad", "SolderMaskAndPasteMaskExpansion", "HeatWelding"],
    "Line": COMMON + ["Net", "Layer", "StartX", "StartY", "EndX", "EndY", "LineWidth"],
    "Arc": COMMON + ["Net", "Layer", "StartX", "StartY", "EndX", "EndY", "LineWidth", "ArcAngle"],
    "Via": COMMON + ["Net", "X", "Y", "Diameter", "HoleDiameter", "ViaType",
        "DesignRuleBlindViaName", "SolderMaskExpansion"],
    "Pour": COMMON + ["Net", "Layer", "ComplexPolygon", "PourFillMethod", "PreserveSilos",
        "PourName", "PourPriority", "LineWidth"],
    "Poured": ["PrimitiveId", "PrimitiveType", "PourPrimitiveId", "PourFills"],
    "Polyline": COMMON + ["Net", "Layer", "Polygon", "LineWidth"],
    "Region": COMMON + ["Layer", "ComplexPolygon", "LineWidth", "RegionName", "RuleType"],
    "Fill": COMMON + ["Net", "Layer", "ComplexPolygon", "LineWidth", "FillMode"],
    "Attribute": COMMON + ["ParentPrimitiveId", "Key", "Value", "X", "Y", "Layer",
        "Rotation", "KeyVisible", "ValueVisible"],
}

_READ = r"""
async function pcbIdentity(){
 const project=await eda.dmt_Project.getCurrentProjectInfo();
 const document=await eda.dmt_SelectControl.getCurrentDocumentInfo();
 if(!project||!document||document.documentType!==3||document.parentProjectUuid!==project.uuid)
  throw new Error('请聚焦同工程的目标PCB');
 return {project_uuid:project.uuid,document_uuid:document.uuid,tab_id:document.tabId,
  editor_version:eda.sys_Environment.getEditorCurrentVersion()};
}
function equal(a,b){return JSON.stringify(a)===JSON.stringify(b);}
function comparablePcb(source){
 const lines=source.split(/\r?\n/).filter(v=>v.trim());const records=[];
 for(let i=0;i<lines.length;i++){
  const line=lines[i];const split=line.indexOf('||');if(split<0)throw new Error('PCB源格式不兼容');
  const env=JSON.parse(line.slice(0,split));const raw=line.slice(split+2).trim();
  if(!raw.endsWith('|')&&i!==lines.length-1)throw new Error('PCB中间记录缺少分隔符');
  const value=JSON.parse(raw.endsWith('|')?raw.slice(0,-1):raw);
  if(i===0){if(env.type!=='DOCHEAD'||value.docType!=='PCB')throw new Error('不是PCB源');
   for(const k of ['client','updateTime','version'])delete value[k];}
  delete env.ticket;records.push([env,value]);
 }
 return JSON.stringify(plain(records));
}
function plain(v){
 if(v===undefined)return null;
 if(v===null||typeof v!=='object')return v;
 if(typeof v.getSource==='function')return plain(v.getSource());
 if(Array.isArray(v))return v.map(plain);
 const out={};for(const k of Object.keys(v).sort())out[k]=plain(v[k]);return out;
}
async function pcbRead(withDrc){
 const identity=await pcbIdentity();
 const source=await eda.sys_FileManager.getDocumentSource();
 if(typeof source!=='string'||!source||source.length>20*1024*1024)throw new Error('PCB完整源不可用或超过20MiB');
 const errors=[];const collections={};let count=0;
 function describe(obj,kind){
  const properties={};const missing=[];
  for(const field of fields[kind]){
   try{const value=obj['getState_'+field]();properties[field]=plain(value);}
   catch(e){missing.push(field+': '+String(e));}
  }
  if(missing.length)errors.push(kind+': '+missing.join(';'));
  return {id:properties.PrimitiveId,kind,properties};
 }
 for(const kind of Object.keys(fields)){
  try{
   const items=await eda['pcb_Primitive'+kind].getAll();
   if(!Array.isArray(items))throw new Error('getAll未返回数组');
   count+=items.length;if(count>50000)throw new Error('PCB图元超过50000限制');
   collections[kind]=[];
   for(const item of items){
    const row=describe(item,kind);
    if(kind==='Component'){
     const pins=await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(row.id);
     if(!Array.isArray(pins))throw new Error('器件焊盘未返回数组');
     count+=pins.length;if(count>50000)throw new Error('PCB图元超过50000限制');
     row.pads=pins.map(p=>describe(p,'Pad'));
     row.bbox=plain(await eda.pcb_Primitive.getPrimitivesBBox([row.id]));
    }
    collections[kind].push(row);
   }
  }catch(e){errors.push(kind+': '+String(e));}
 }
 const outputs={};
 async function read(name,fn){
  try{const v=await fn();if(v===undefined)throw new Error('返回undefined');outputs[name]={status:'returned',value:plain(v)};}
  catch(e){outputs[name]={status:'error',error:String(e)};}
 }
 await read('layers',()=>eda.pcb_Layer.getAllLayers());
 await read('footprints',()=>eda.sys_FileManager.getDocumentFootprintSources());
 await read('stackup',()=>eda.pcb_Layer.getCurrentPhysicalStackingConfiguration());
 await read('origin',()=>eda.pcb_Document.getCanvasOrigin());
 await read('board',()=>eda.dmt_Board.getCurrentBoardInfo());
 await read('nets',()=>eda.pcb_Net.getAllNets());
 await read('netlist',()=>eda.pcb_Net.getNetlist());
 await read('rules',()=>eda.pcb_Drc.getCurrentRuleConfiguration());
 await read('net_rules',()=>eda.pcb_Drc.getNetRules());
 await read('net_classes',()=>eda.pcb_Drc.getAllNetClasses());
 await read('differential_pairs',()=>eda.pcb_Drc.getAllDifferentialPairs());
 await read('equal_length_groups',()=>eda.pcb_Drc.getAllEqualLengthNetGroups());
 if(withDrc)await read('drc',()=>eda.pcb_Drc.check(true,false,true));
 const layers={};const enums=typeof EPCB_LayerId==='undefined'?gatewayEnums.LayerId:EPCB_LayerId;
 for(const name of layerNames){
  if(typeof enums[name]==='number')layers[name]=enums[name];
 }
 if(!equal(identity,await pcbIdentity()))throw new Error('PCB采集期间目标变化');
 const sourceAfter=await eda.sys_FileManager.getDocumentSource();
 if(comparablePcb(source)!==comparablePcb(sourceAfter))errors.push('PCB采集期间完整源变化；保留前后原件用于诊断');
 return {identity,source,collections,outputs,layer_ids:layers,read_errors:errors,
  source_after:sourceAfter,
  complete:errors.length===0&&Object.entries(outputs).every(([k,v])=>k==='stackup'||v.status==='returned'),api_unit:'mil',coordinate_system:'data-origin',
  stackup_available:outputs.stackup.status==='returned',enum_source:typeof EPCB_LayerId==='undefined'?'documented-gateway-projection':'runtime-sdk',
  geometry_scope:'known-primitive-APIs-plus-complete-document-source'};
}
"""


def _preamble():
    return "const gatewayEnums=" + json.dumps(GATEWAY_ENUMS) + ";const fields=" + json.dumps(FIELDS) + ";const layerNames=" + json.dumps(LAYER_NAMES) + ";" + _READ


def capture_script(with_drc=True):
    return _preamble() + "return await pcbRead(" + ("true" if with_drc else "false") + ");"


def operation_script(before, operation):
    # Python validation is mandatory even when this helper is used outside CLI.
    from .pcb import validate_operation
    validate_operation(operation)
    from .pcb import validate_snapshot
    validate_snapshot(before)
    payload = {"identity": before["identity"], "source": before["source"], "op": operation,
               "collections": before["collections"], "outputs": {k:v for k,v in before["outputs"].items() if k != "drc"}}
    return _preamble() + "const input=" + json.dumps(payload, ensure_ascii=False, allow_nan=False) + r""";
let attempted=false;
try{
 const current=await pcbRead(false);
 if(!current.complete||!equal(input.identity,current.identity)||comparablePcb(input.source)!==comparablePcb(current.source)||
  !equal(input.collections,current.collections)||!equal(input.outputs,current.outputs))
  throw new Error('PCB操作前完整基线已变化或读取不完整');
 const op=input.op;const p=op.parameters;let created=null;let engineResult=null;
 function layer(name){
  const enums=typeof EPCB_LayerId==='undefined'?gatewayEnums.LayerId:EPCB_LayerId;
  if(typeof enums[name]!=='number')throw new Error('PCB图层枚举不可用');
  return enums[name];
 }
 if(op.operation==='move_component'){
  const primitive=await eda.pcb_PrimitiveComponent.get(p.primitive_id);
  if(!primitive||primitive.getState_PrimitiveLock())throw new Error('器件不存在或已锁定');
  const pending=primitive.toAsync();
  pending.setState_X(p.x);pending.setState_Y(p.y);pending.setState_Rotation(p.rotation);
  attempted=true;await pending.done();
 }else if(op.operation==='add_line'){
  const l=layer(p.layer);attempted=true;
  created=await eda.pcb_PrimitiveLine.create(p.net,l,p.start[0],p.start[1],p.end[0],p.end[1],p.width,false);
 }else if(op.operation==='add_via'){
  const viaType=typeof EPCB_PrimitiveViaType==='undefined'?gatewayEnums.ViaType:EPCB_PrimitiveViaType;
  attempted=true;created=await eda.pcb_PrimitiveVia.create(p.net,p.x,p.y,p.hole,p.diameter,viaType.VIA,null,null,false);
 }else if(op.operation==='add_outline'){
  const l=layer('BOARD_OUTLINE');const polygon=eda.pcb_MathPolygon.createPolygon(p.polygon);
  if(!polygon)throw new Error('板框多边形不可用');
  attempted=true;created=await eda.pcb_PrimitivePolyline.create('',l,polygon,p.width,false);
 }else if(op.operation==='add_pour'){
  const l=layer(p.layer);const polygon=eda.pcb_MathPolygon.createPolygon(p.polygon);
  if(!polygon)throw new Error('铺铜多边形不可用');
  const fill=typeof EPCB_PrimitivePourFillMethod==='undefined'?gatewayEnums.PourFillMethod:EPCB_PrimitivePourFillMethod;
  attempted=true;created=await eda.pcb_PrimitivePour.create(p.net,l,polygon,fill.SOLID,false,p.name,p.priority,p.width,false);
 }else if(op.operation==='rebuild_pour'){
  const primitive=await eda.pcb_PrimitivePour.get(p.primitive_id);
  if(!primitive)throw new Error('铜区不存在');
  attempted=true;created=await primitive.rebuildCopperRegion();
 }else if(op.operation==='route_nets'){
  if(!p.nets.length)throw new Error('拒绝空网络列表触发全板布线');
  const layers=p.layers.map(layer);
  const keep=typeof EPCB_AutoRoutingExistingPrimitiveMode==='undefined'?gatewayEnums.RoutingExistingPrimitiveMode:EPCB_AutoRoutingExistingPrimitiveMode;
  attempted=true;engineResult=await eda.pcb_Document.autoRouting({RoutingNets:p.nets,layers,
   existingPrimitiveMode:keep.KEEP});
 }else if(op.operation==='auto_place'){
  attempted=true;engineResult=await eda.pcb_Document.autoLayout();
 }else{throw new Error('未知PCB固定操作');}
 const createdId=created?created.getState_PrimitiveId():null;
 if(['add_line','add_via','add_outline','add_pour','rebuild_pour'].includes(op.operation)&&!createdId)
  throw new Error('PCB写入未返回图元身份');
 const after=await pcbRead(true);
 return {status:'observed',attempted,created_id:createdId,engine_result:plain(engineResult),after};
}catch(e){return {status:attempted?'uncertain':'blocked',attempted,error:String(e)};}
"""


def export_script(*, include_ipc=True):
    # Returned binary files remain bound to the same source; never place orders.
    return _preamble() + 'const includeIpc='+json.dumps(include_ipc)+';'+r"""
const identity=await pcbIdentity();const source=await eda.sys_FileManager.getDocumentSource();
const files={};
for(const [name,fn] of [
 ['gerber',()=>eda.pcb_ManufactureData.getGerberFile('pcb')],
 ['bom',()=>eda.pcb_ManufactureData.getBomFile('pcb','csv')],
 ['pick_and_place',()=>eda.pcb_ManufactureData.getPickAndPlaceFile('pcb','csv')],
 ['ipc_d356',()=>eda.pcb_ManufactureData.getIpcD356AFile('pcb')]].filter(([name])=>includeIpc||name!=='ipc_d356')){
 const file=await fn();if(!file||file.size>30*1024*1024)throw new Error('制造文件不可用或超过30MiB: '+name);
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
 for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
 files[name]={name:file.name,size:bytes.length,base64:btoa(binary)};
}
const sourceAfter=await eda.sys_FileManager.getDocumentSource();
const stable=equal(identity,await pcbIdentity())&&comparablePcb(source)===comparablePcb(sourceAfter);
return {identity,source,source_after:sourceAfter,stable,files};
"""


def additions_script(before, operations):
    """Fixed bounded additions with immediate object getters and a full batch read.

    Only the explicitly delegated test helper uses this experimental batching.
    No deletions, modifications, engine routing, or source writes are supported.
    """
    from .pcb import validate_operation, validate_snapshot
    validate_snapshot(before)
    if not 1 <= len(operations) <= 300:
        raise ValueError('Expected 1..300 explicit additions')
    for op in operations:
        validate_operation(op)
        if op['operation'] not in ('add_line', 'add_via'):
            raise ValueError('Batched additions only support line/via')
    payload={'identity':before['identity'],'source':before['source'],'operations':operations,
             'collections':before['collections'],'outputs':{k:v for k,v in before['outputs'].items() if k!='drc'}}
    return _preamble()+'const input='+json.dumps(payload,ensure_ascii=False,allow_nan=False)+r""";
const steps=[];let attempted=false;
function near(a,b){return typeof a==='number'&&typeof b==='number'&&Math.abs(a-b)<=1e-8;}
try{
 const current=await pcbRead(false);
 if(!current.complete||!equal(input.identity,current.identity)||comparablePcb(input.source)!==comparablePcb(current.source)||
  !equal(input.collections,current.collections)||!equal(input.outputs,current.outputs))throw new Error('PCB批次前完整基线变化');
 for(const op of input.operations){
  const p=op.parameters;let created=null;let category;
  if(op.operation==='add_line'){
   category='Line';attempted=true;
   created=await eda.pcb_PrimitiveLine.create(p.net,gatewayEnums.LayerId[p.layer],p.start[0],p.start[1],p.end[0],p.end[1],p.width,false);
  }else if(op.operation==='add_via'){
   category='Via';attempted=true;
   created=await eda.pcb_PrimitiveVia.create(p.net,p.x,p.y,p.hole,p.diameter,gatewayEnums.ViaType.VIA,null,null,false);
  }else throw new Error('非固定新增操作');
  if(!created)throw new Error('未返回新增图元');
  const id=created.getState_PrimitiveId();if(!id)throw new Error('新增图元缺少身份');
  const obj=await eda['pcb_Primitive'+category].get(id);
  if(!obj){steps.push({operation_id:op.id,created_id:id,category,properties:null,normalized:true});continue;}
  const props={};for(const field of fields[category])props[field]=plain(obj['getState_'+field]());
  if(props.Net!==p.net)throw new Error('新增图元网络不符');
  if(category==='Line'&&(props.Layer!==gatewayEnums.LayerId[p.layer]||!near(props.StartX,p.start[0])||!near(props.StartY,p.start[1])||!near(props.EndX,p.end[0])||!near(props.EndY,p.end[1])||!near(props.LineWidth,p.width)))throw new Error('新增线段参数不符');
  if(category==='Via'&&(!near(props.X,p.x)||!near(props.Y,p.y)||!near(props.HoleDiameter,p.hole)||!near(props.Diameter,p.diameter)))throw new Error('新增过孔参数不符');
  steps.push({operation_id:op.id,created_id:id,category,properties:props});
 }
 return {status:'observed',attempted,steps,after:await pcbRead(true)};
}catch(e){return {status:attempted?'uncertain':'blocked',attempted,steps,error:String(e)};}
"""


def pours_script(before, polygon):
    from .pcb import validate_snapshot
    validate_snapshot(before)
    if not isinstance(polygon,list) or len(polygon)<9 or polygon[2]!='L' or polygon[-2]!=polygon[0] or polygon[-1]!=polygon[1]:
        raise ValueError('Closed board pour polygon required')
    payload={'identity':before['identity'],'source':before['source'],'collections':before['collections'],
             'outputs':{k:v for k,v in before['outputs'].items() if k!='drc'},'polygon':polygon}
    return _preamble()+'const input='+json.dumps(payload,ensure_ascii=False,allow_nan=False)+r""";
try{
 const current=await pcbRead(false);
 if(!current.complete||!equal(input.identity,current.identity)||comparablePcb(input.source)!==comparablePcb(current.source)||!equal(input.collections,current.collections)||!equal(input.outputs,current.outputs))throw new Error('PCB铺铜前基线变化');
 const polygon=eda.pcb_MathPolygon.createPolygon(input.polygon);if(!polygon)throw new Error('板框铺铜多边形无效');
 const fill=typeof EPCB_PrimitivePourFillMethod==='undefined'?gatewayEnums.PourFillMethod:EPCB_PrimitivePourFillMethod;
 const created=[];
 for(const [layer,name] of [[gatewayEnums.LayerId.TOP,'GND_TOP'],[gatewayEnums.LayerId.BOTTOM,'GND_BOTTOM']]){
  const pour=await eda.pcb_PrimitivePour.create('GND',layer,polygon,fill.SOLID,false,name,1,0.2,false);
  if(!pour)throw new Error('铺铜未返回图元: '+name);
  const id=pour.getState_PrimitiveId();if(!id)throw new Error('铺铜缺少图元身份');
  const obj=await eda.pcb_PrimitivePour.get(id);if(!obj)throw new Error('铺铜无法立即回读: '+name);
  created.push({id,layer,name,net:obj.getState_Net(),width:obj.getState_LineWidth(),polygon:plain(obj.getState_ComplexPolygon())});
 }
 return {status:'observed',created,after:await pcbRead(true)};
}catch(e){return {status:'uncertain',error:String(e)};}
"""


def rebuild_pours_script(before, pour_ids):
    from .pcb import validate_snapshot
    validate_snapshot(before)
    if not pour_ids or len(pour_ids)>4 or any(not isinstance(v,str) or not v for v in pour_ids):
        raise ValueError('Expected one to four pour IDs')
    payload={'identity':before['identity'],'source':before['source'],'collections':before['collections'],
             'outputs':{k:v for k,v in before['outputs'].items() if k!='drc'},'pour_ids':pour_ids}
    return _preamble()+'const input='+json.dumps(payload,ensure_ascii=False,allow_nan=False)+r""";
try{
 const current=await pcbRead(false);
 if(!current.complete||!equal(input.identity,current.identity)||comparablePcb(input.source)!==comparablePcb(current.source)||!equal(input.collections,current.collections)||!equal(input.outputs,current.outputs))throw new Error('重建铺铜前基线变化');
 const results=[];
 for(const id of input.pour_ids){const pour=await eda.pcb_PrimitivePour.get(id);if(!pour)throw new Error('铜区不存在: '+id);results.push({id,result:plain(await pour.rebuildCopperRegion())});}
 await new Promise(r=>setTimeout(r,800));
 return {status:'observed',results,after:await pcbRead(true)};
}catch(e){return {status:'uncertain',error:String(e)};}
"""


def native_route_script(before, nets, job_id, *, together=False):
    from .pcb import validate_snapshot
    validate_snapshot(before)
    limit=80 if together is True else 20
    if not isinstance(together,bool) or not isinstance(nets,list) or not 1<=len(nets)<=limit or any(not isinstance(v,str) or not v for v in nets) or len(set(nets))!=len(nets):
        raise ValueError('Expected distinct explicit native route nets within the selected batch limit')
    payload={'identity':before['identity'],'source':before['source'],'collections':before['collections'],
             'outputs':{k:v for k,v in before['outputs'].items() if k!='drc'},'nets':nets,'job_id':job_id,'together':together}
    return _preamble()+'const input='+json.dumps(payload,ensure_ascii=False,allow_nan=False)+r""";
try{
 const current=await pcbRead(false);
 if(!current.complete||!equal(input.identity,current.identity)||comparablePcb(input.source)!==comparablePcb(current.source)||!equal(input.collections,current.collections)||!equal(input.outputs,current.outputs))throw new Error('原生布线前基线变化');
 const keep=typeof EPCB_AutoRoutingExistingPrimitiveMode==='undefined'?gatewayEnums.RoutingExistingPrimitiveMode:EPCB_AutoRoutingExistingPrimitiveMode;
 const optimization=typeof EPCB_AutoRoutingOptimization==='undefined'?gatewayEnums.RoutingOptimization:EPCB_AutoRoutingOptimization;
 const state={job_id:input.job_id,identity:input.identity,nets:input.nets,status:'queued',results:[]};
 if(eda.sys_Storage.getExtensionUserConfig(input.job_id)!==undefined)throw new Error('路由任务已存在');
 if(!await eda.sys_Storage.setExtensionUserConfig(input.job_id,state))throw new Error('无法保存路由任务状态');
 async function run(){
  try{
   const groups=input.together?[input.nets]:input.nets.map(net=>[net]);
   for(const group of groups){
    if(!equal(input.identity,await pcbIdentity()))throw new Error('路由目标发生变化');
    state.status='routing';state.current_net=group.length===1?group[0]:null;state.current_nets=group;await eda.sys_Storage.setExtensionUserConfig(input.job_id,state);
    const result=await eda.pcb_Document.autoRouting({RoutingNets:group,layers:[gatewayEnums.LayerId.TOP,gatewayEnums.LayerId.BOTTOM],existingPrimitiveMode:keep.KEEP,optimization:input.together?optimization.COMPLETION:optimization.FASTER});
    state.results.push({net:group.length===1?group[0]:null,nets:group,result:plain(result)});await eda.sys_Storage.setExtensionUserConfig(input.job_id,state);
    if(!result||typeof result!=='object')throw new Error('布线接口未返回有效结果；需独立读取PCB，不得记作布线完成');
   }
   state.status='completed';state.current_net=null;
  }catch(e){state.status='failed';state.error=String(e);}
  await eda.sys_Storage.setExtensionUserConfig(input.job_id,state);
 }
 run();
 return {status:'started',job_id:input.job_id};
}catch(e){return {status:'uncertain',error:String(e)};}
"""


def repair_delete_script(before, removals):
    from .pcb import validate_snapshot
    validate_snapshot(before)
    if not removals or any(k not in ('Line','Via','Poured','Pour') for k in removals):raise ValueError('Unsupported repair deletion')
    removed_pours=set(removals.get('Pour',[]))
    derived={r['id'] for r in before['collections']['Poured'] if r['properties'].get('PourPrimitiveId') in removed_pours}
    if not derived<=set(removals.get('Poured',[])):raise ValueError('Pour deletion requires explicitly enumerated derived fills')
    allowed={k:{r['id'] for r in before['collections'][k]} for k in removals}
    if any(not isinstance(ids,list) or not set(ids)<=allowed[k] for k,ids in removals.items()):raise ValueError('Repair objects absent from baseline')
    payload={'identity':before['identity'],'source':before['source'],'collections':before['collections'],
             'outputs':{k:v for k,v in before['outputs'].items() if k!='drc'},'removals':removals}
    return _preamble()+'const input='+json.dumps(payload,ensure_ascii=False,allow_nan=False)+r""";
let attempted=false;
try{
 const current=await pcbRead(false);
 if(!current.complete||!equal(input.identity,current.identity)||comparablePcb(input.source)!==comparablePcb(current.source)||!equal(input.collections,current.collections)||!equal(input.outputs,current.outputs))throw new Error('局部修复前基线变化');
 const results=[];
 for(const [kind,ids] of Object.entries(input.removals)){
  if(!ids.length)continue;
  attempted=true;const ok=await eda['pcb_Primitive'+kind].delete(ids);
  if(!ok)throw new Error('局部删除未确认: '+kind);
  results.push({kind,ids,ok});
 }
 return {status:'observed',attempted,results,after:await pcbRead(true)};
}catch(e){return {status:attempted?'uncertain':'blocked',attempted,error:String(e)};}
"""
