"""Bounded schematic adapter; signatures checked with easyeda-api-skill 1.1.29.

Only fixed code ships to the gateway. JSON data is encoded as a string literal
and parsed, including keys such as __proto__; no user text becomes source code.
"""
from __future__ import annotations

import json
import math

MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_OPERATIONS = 50
SUPPORTED_OPERATIONS = ["place_component", "add_wire", "place_net_flag"]
VERIFIED_EDITOR_VERSION = "3.2.149.88089769"
ROTATION_CONVENTION = "easyeda-pro-api-0.2.53-negated-create"

_IDENTITY = r"""
async function identity(){
 const project=await eda.dmt_Project.getCurrentProjectInfo();
 const document=await eda.dmt_SelectControl.getCurrentDocumentInfo();
 if(!project||!document||document.documentType!==1||document.parentProjectUuid!==project.uuid)throw new Error('目标不是同工程的原理图页');
 for(const value of [project.uuid,document.uuid,document.tabId])if(typeof value!=='string'||!value.trim())throw new Error('目标身份缺失');
 return {project_uuid:project.uuid,document_uuid:document.uuid,tab_id:document.tabId,document_type:document.documentType};
}
function same(a,b){return JSON.stringify(a)===JSON.stringify(b);}
function comparable(value){
 const end=value.indexOf('\n');const line=end<0?value:value.slice(0,end);
 const separator=line.indexOf('||');
 if(separator<0)return value;
 let outer;try{outer=JSON.parse(line.slice(0,separator));}catch(error){return value;}
 if(outer.type!=='DOCHEAD')return value;
 const record=line.slice(separator+2).trimEnd();
 if(!record.endsWith('|'))throw new Error('不兼容的 DOCHEAD 结构');
 const header=JSON.parse(record.slice(0,-1));
 if(header.docType!=='SCH_PAGE')throw new Error('文档源不是原理图页');
 const filtered={};
 for(const key of Object.keys(header).sort())if(!['client','updateTime','version'].includes(key))filtered[key]=header[key];
 return line.slice(0,separator+2)+JSON.stringify(filtered)+'|'+(end<0?'':value.slice(end));
}
async function source(){
 const value=await eda.sys_FileManager.getDocumentSource();
 if(typeof value!=='string'||!value.trim()||value.length>20971520)throw new Error('文档源缺失或过大');
 return value;
}
async function stable(expected){
 const before=await identity();
 if(expected&&!same(before,expected))throw new Error('目标文档与方案不一致');
 const first=await source();
 const second=await source();
 const after=await identity();
 if(!same(before,after)||comparable(first)!==comparable(second))throw new Error('采集期间目标或文档内容变化');
 return {identity:after,source:second};
}
"""

SNAPSHOT_QUERY = _IDENTITY + "const state=await stable();return {domain:'schematic',...state};"

PROJECT_LIBRARY_QUERY = _IDENTITY + """
const before=await stable();
const file=await eda.sys_FileManager.getProjectFile(undefined,undefined,'epro2');
if(!file||!Number.isFinite(file.size)||file.size>52428800)throw new Error('工程文件缺失或超过50MiB');
const info=await eda.sys_FileManager.extractProjectInfo(file);
if(!info||typeof info!=='object'||Array.isArray(info))throw new Error('工程配置信息不兼容');
const serialized=JSON.stringify(info);
if(serialized.length>20971520)throw new Error('工程配置信息超过20MiB');
const bytes=new Uint8Array(await file.arrayBuffer());
if(bytes.length!==file.size||bytes.length>52428800)throw new Error('工程文件大小不一致');
let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
const archive_base64=btoa(binary);
const after=await stable(before.identity);
if(comparable(before.source)!==comparable(after.source))throw new Error('读取工程库期间文档内容变化');
return {identity:after.identity,source:after.source,project_info:info,file_name:file.name,file_size:file.size,archive_base64,
 current_editor_version:eda.sys_Environment.getEditorCurrentVersion()};
"""

SVG_QUERY = _IDENTITY + """
const before=await stable();
let file;
if(typeof eda.sch_ManufactureData.getExportDocumentFile==='function')
 file=await eda.sch_ManufactureData.getExportDocumentFile('design-assist-review','SVG',undefined,'Current Schematic Page');
else if(typeof eda.sch_ManufactureData.getSvgFile==='function')file=await eda.sch_ManufactureData.getSvgFile('design-assist-review');
else throw new Error('当前编辑器不支持原生SVG导出；请使用客户端截图审核');
if(!file||!Number.isFinite(file.size)||file.size>10485760)throw new Error('SVG缺失或超过10MiB');
const svg=await file.text();
if(svg.length>10485760||!svg.includes('<svg'))throw new Error('SVG内容不兼容');
const after=await stable(before.identity);
if(comparable(before.source)!==comparable(after.source))throw new Error('SVG导出期间文档变化');
return {identity:after.identity,source:after.source,svg,file_name:file.name,file_size:file.size};
"""

COMPONENT_QUERY = _IDENTITY + """
const before=await stable();
const objects=await eda.sch_PrimitiveComponent.getAll(undefined,false);
if(!Array.isArray(objects))throw new Error('器件列表不兼容');
const components=objects.slice(0,2000).map(item=>({primitive_id:item.getState_PrimitiveId(),
 component_type:item.getState_ComponentType(),
 component:item.getState_Component(),designator:item.getState_Designator(),
 x:item.getState_X(),y:item.getState_Y(),rotation:item.getState_Rotation(),mirror:item.getState_Mirror(),
 sub_part_name:item.getState_SubPartName()??null,
 add_into_bom:item.getState_AddIntoBom(),add_into_pcb:item.getState_AddIntoPcb()}));
const after=await stable(before.identity);
if(comparable(before.source)!==comparable(after.source))throw new Error('读取器件期间文档内容变化');
return {identity:after.identity,source:after.source,components,total:objects.length,truncated:objects.length>components.length};
"""

_PLACE_QUERY = _IDENTITY + """
let attempted=false;let primitiveId=null;let observed=null;let pins=null;
try{
 const before=await stable(input.identity);
 if(comparable(before.source)!==comparable(input.source))throw new Error('文档源已变化，请重新准备方案');
 const p=input.parameters;
 if(eda.sys_Environment.getEditorCurrentVersion()!==input.editor_version)throw new Error('编辑器版本尚未核验');
 if(!same(await identity(),input.identity))throw new Error('写入前目标变化');
 attempted=true;
 const created=await eda.sch_PrimitiveComponent.create(
  {libraryUuid:p.library_uuid,uuid:p.device_uuid},p.x,p.y,
  p.sub_part_name===null?undefined:p.sub_part_name,p.requested_api_rotation,p.mirror,p.add_into_bom,p.add_into_pcb);
 if(!created)throw new Error('创建没有返回器件，实际写入状态未知');
 primitiveId=created.getState_PrimitiveId();
 if(typeof primitiveId!=='string'||!primitiveId.trim())throw new Error('创建未返回图元ID');
 if(!same(await identity(),input.identity))throw new Error('创建后目标变化');
 const item=await eda.sch_PrimitiveComponent.get(primitiveId);
 if(!item||item.getState_PrimitiveId()!==primitiveId)throw new Error('无法回读新建器件');
 const component=item.getState_Component();
 const subPart=item.getState_SubPartName();
 observed={primitive_id:primitiveId,library_uuid:component?.libraryUuid,device_uuid:component?.uuid,
  x:item.getState_X(),y:item.getState_Y(),rotation:item.getState_Rotation(),mirror:item.getState_Mirror(),
  add_into_bom:item.getState_AddIntoBom(),add_into_pcb:item.getState_AddIntoPcb(),
  sub_part_name:subPart===undefined?null:subPart};
 for(const key of ['library_uuid','x','y','mirror','add_into_bom','add_into_pcb','sub_part_name']){
  if(observed[key]!==p[key])throw new Error('器件回读不一致: '+key);
 }
 if(observed.device_uuid!==p.expected_project_device_uuid)throw new Error('工程器件UUID回读不一致');
 if(observed.rotation!==p.expected_canvas_rotation)throw new Error('画布角度回读不一致');
 const objects=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(primitiveId);
 if(!Array.isArray(objects)||objects.length!==p.expected_pins.length)throw new Error('器件引脚数量不一致');
 pins=objects.map(pin=>({primitive_id:pin.getState_PrimitiveId(),number:pin.getState_PinNumber(),x:pin.getState_X(),y:pin.getState_Y()}));
 if(pins.some(pin=>typeof pin.primitive_id!=='string'||!pin.primitive_id.trim())||new Set(pins.map(pin=>pin.primitive_id)).size!==pins.length||
  new Set(pins.map(pin=>pin.number)).size!==pins.length)throw new Error('器件引脚身份不兼容');
 for(const expected of p.expected_pins){
  const pin=pins.find(pin=>pin.number===expected.number);
  if(!pin||pin.x!==expected.x||pin.y!==expected.y)throw new Error('器件引脚位置不一致: '+expected.number);
 }
 const after=await stable(input.identity);
 if(comparable(after.source)===comparable(before.source))throw new Error('创建后文档源未变化，不能确认结果');
 return {status:'verified',attempted,primitive_id:primitiveId,observed,pins,...after};
}catch(error){return {status:attempted?'uncertain':'blocked',attempted,primitive_id:primitiveId,observed,pins,error:String(error).slice(0,2000)};}
"""


def validate_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1 or snapshot.get("kind") != "drawing-source":
        raise ValueError("不是完整文档源绘图快照")
    if snapshot.get("domain") != "schematic":
        raise ValueError("当前写入适配器仅支持原理图")
    if not isinstance(snapshot.get("window_id"), str) or not snapshot["window_id"].strip():
        raise ValueError("绘图快照缺少窗口 ID")
    identity = snapshot.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"project_uuid", "document_uuid", "tab_id", "document_type"}:
        raise ValueError("绘图快照目标身份格式无效")
    if type(identity["document_type"]) is not int or identity["document_type"] != 1 or any(
        not isinstance(identity[k], str) or not identity[k].strip() for k in ("project_uuid", "document_uuid", "tab_id")
    ):
        raise ValueError("绘图快照目标身份不完整")
    value = snapshot.get("source")
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("完整文档源缺失或超过 20MiB")


def comparable_source(value: str) -> str:
    """Ignore only volatile fields empirically observed in the first DOCHEAD.

    All following source bytes, including other headers, remain significant.
    """
    line, newline, tail = value.partition("\n")
    prefix, separator, record = line.partition("||")
    if not separator:
        return value
    try:
        outer = json.loads(prefix)
    except ValueError:
        return value
    if not isinstance(outer, dict) or outer.get("type") != "DOCHEAD":
        return value
    record = record.rstrip()
    if not record.endswith("|"):
        raise ValueError("不兼容的 DOCHEAD 结构")
    header = json.loads(record[:-1])
    if not isinstance(header, dict) or header.get("docType") != "SCH_PAGE":
        raise ValueError("文档源不是原理图页")
    stable_header = {k: v for k, v in header.items() if k not in {"client", "updateTime", "version"}}
    return prefix + separator + json.dumps(stable_header, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "|" + newline + tail


def validate_parameters(p: dict) -> None:
    fields = {"library_uuid", "device_uuid", "x", "y", "unit", "requested_api_rotation", "mirror",
              "add_into_bom", "add_into_pcb", "sub_part_name", "expected_project_device_uuid",
              "expected_canvas_rotation", "rotation_convention", "expected_pins", "mapping_evidence"}
    if not isinstance(p, dict) or set(p) != fields:
        raise ValueError("place_component parameters 字段必须精确为：" + ", ".join(sorted(fields)))
    for key in ("library_uuid", "device_uuid", "expected_project_device_uuid"):
        if not isinstance(p[key], str) or not p[key].strip() or len(p[key]) > 256 or "|" in p[key]:
            raise ValueError(key + " 必须是已核验的器件库标识")
    if p["unit"] != "10mil":
        raise ValueError("原理图坐标必须明确声明 unit=10mil")
    for key in ("x", "y"):
        if type(p[key]) not in (int, float) or not math.isfinite(p[key]) or abs(p[key]) > 1000000:
            raise ValueError(key + " 必须是有限坐标，绝对值不超过 1000000（10mil）")
    for key in ("requested_api_rotation", "expected_canvas_rotation"):
        if type(p[key]) not in (int, float) or p[key] not in (0, 90, 180, 270):
            raise ValueError(key + " 必须为 0/90/180/270 度")
    if p["rotation_convention"] != ROTATION_CONVENTION or p["expected_canvas_rotation"] != (-p["requested_api_rotation"]) % 360:
        raise ValueError("角度约定或 API→画布角度换算不一致")
    evidence = p["mapping_evidence"]
    if (not isinstance(evidence, dict) or set(evidence) != {"path", "sha256"} or
            not isinstance(evidence["path"], str) or not evidence["path"].strip() or
            not isinstance(evidence["sha256"], str) or len(evidence["sha256"]) != 64 or
            any(c not in "0123456789abcdef" for c in evidence["sha256"])):
        raise ValueError("mapping_evidence 必须包含工程导出证据路径及 SHA256")
    pins = p["expected_pins"]
    if not isinstance(pins, list) or not 1 <= len(pins) <= 500:
        raise ValueError("expected_pins 必须包含 1–500 个已核验引脚")
    numbers = set()
    for pin in pins:
        if (not isinstance(pin, dict) or set(pin) != {"number", "x", "y"} or
                not isinstance(pin["number"], str) or not pin["number"].strip() or pin["number"] in numbers or
                any(type(pin[k]) not in (int, float) or not math.isfinite(pin[k]) or abs(pin[k]) > 1000000 for k in ("x", "y"))):
            raise ValueError("预期引脚编号必须唯一、坐标必须有限")
        numbers.add(pin["number"])
    for key in ("mirror", "add_into_bom", "add_into_pcb"):
        if type(p[key]) is not bool:
            raise ValueError(key + " 必须为布尔值")
    if p["sub_part_name"] is not None and (not isinstance(p["sub_part_name"], str) or not p["sub_part_name"].strip() or len(p["sub_part_name"]) > 128):
        raise ValueError("sub_part_name 必须为明确子部件名称或 null（单部件）")


def placement_script(snapshot: dict, parameters: dict) -> str:
    validate_snapshot(snapshot)
    validate_parameters(parameters)
    payload = {"identity": snapshot["identity"], "source": snapshot["source"], "parameters": parameters,
               "editor_version": VERIFIED_EDITOR_VERSION}
    literal = json.dumps(json.dumps(payload, ensure_ascii=True, allow_nan=False), ensure_ascii=True)
    return "const input=JSON.parse(" + literal + ");" + _PLACE_QUERY


def device_search_script(keyword: str) -> str:
    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 200:
        raise ValueError("器件搜索词必须是 1–200 字符")
    literal = json.dumps(keyword, ensure_ascii=True)
    return ("const items=await eda.lib_Device.search(" + literal +
            ",undefined,undefined,undefined,10,1);if(!Array.isArray(items))throw new Error('器件搜索格式不兼容');return {items};")


def device_details_script(library_uuid: str, device_uuid: str) -> str:
    for value in (library_uuid, device_uuid):
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError("器件及库 UUID 必须是 1–256 字符")
    return ("const item=await eda.lib_Device.get(" + json.dumps(device_uuid, ensure_ascii=True) +
            "," + json.dumps(library_uuid, ensure_ascii=True) +
            ");if(!item)throw new Error('未找到器件');return {item};")


def device_archive_script(library_uuid: str, device_uuid: str) -> str:
    """Read an elibz2 library archive without importing or activating it.

    SYS_FileManager.getDeviceFileByDeviceUuid signature verified in skill 1.1.29.
    The bounded archive is evidence for symbol pin geometry and library identity.
    """
    for value in (library_uuid, device_uuid):
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError("器件及库 UUID 必须是 1–256 字符")
    payload = json.dumps(json.dumps([device_uuid, library_uuid]), ensure_ascii=True)
    return "const ids=JSON.parse(" + payload + ");" + r"""
const file=await eda.sys_FileManager.getDeviceFileByDeviceUuid(ids[0],ids[1],'elibz2');
if(!file||!Number.isFinite(file.size)||file.size>10485760)throw new Error('库文件缺失或超过10MiB');
const bytes=new Uint8Array(await file.arrayBuffer());
if(bytes.length!==file.size)throw new Error('库文件大小不一致');
let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
return {device_uuid:ids[0],library_uuid:ids[1],file_name:file.name,file_size:file.size,archive_base64:btoa(binary)};
"""


def component_pins_script(primitive_id: str) -> str:
    """Read one canvas component's pins; never interpret a netlist key as its ID."""
    if not isinstance(primitive_id, str) or not primitive_id.strip() or len(primitive_id) > 256:
        raise ValueError("图元 ID 必须为 1–256 字符")
    return "const primitiveId=" + json.dumps(primitive_id, ensure_ascii=True) + ";" + _IDENTITY + r"""
const before=await stable();
const component=await eda.sch_PrimitiveComponent.get(primitiveId);
if(!component||component.getState_PrimitiveId()!==primitiveId)throw new Error('目标器件不存在或图元 ID 不一致');
const objects=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(primitiveId);
if(!Array.isArray(objects))throw new Error('器件引脚不可用，不当作零引脚');
const pins=objects.slice(0,500).map(pin=>({
 primitive_id:pin.getState_PrimitiveId(),number:pin.getState_PinNumber(),name:pin.getState_PinName(),
 x:pin.getState_X(),y:pin.getState_Y(),rotation:pin.getState_Rotation(),length:pin.getState_PinLength(),
 no_connected:pin.getState_NoConnected()??null
}));
if(pins.some(pin=>typeof pin.primitive_id!=='string'||!pin.primitive_id.trim()||typeof pin.number!=='string'||
 typeof pin.name!=='string'||[pin.x,pin.y,pin.rotation,pin.length].some(value=>typeof value!=='number'||!Number.isFinite(value)))||
 new Set(pins.map(pin=>pin.primitive_id)).size!==pins.length)throw new Error('器件引脚字段不兼容');
const after=await stable(before.identity);
if(comparable(before.source)!==comparable(after.source))throw new Error('读取引脚期间文档内容变化');
return {identity:after.identity,source:after.source,component_primitive_id:primitiveId,
 designator:component.getState_Designator()??null,pins,total:objects.length,truncated:objects.length>pins.length,
 limitations:['坐标和长度为 API 原值，文档单位 10mil 尚待画布校准；不据此推断网络连接。',
 '图元 ID 与网表元件键分开保存；名称和引脚号不证明工程正确性。']};
"""
