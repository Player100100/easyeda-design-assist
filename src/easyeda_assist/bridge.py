"""Fixed operations over the existing API Gateway HTTP protocol.

No dependency on audit project's process lifecycle or state directory.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .storage import now

# Signatures checked against easyeda-api-skill 1.1.29. Numeric document
# types: SCHEMATIC_PAGE=1, PCB=3. This script never activates a document.
MAX_SELECTED_DETAILS = 100
# Each getter is checked against its concrete primitive class, never inferred
# from a user-provided name or from arbitrary methods on an object.
_COMPONENT_FIELDS = ["Designator", "Name", "Manufacturer", "ManufacturerId",
                     "Supplier", "SupplierId", "OtherProperty", "X", "Y",
                     "Rotation", "Component", "Footprint"]
_READ_FIELDS = {
    "schematic": {
        "Component": _COMPONENT_FIELDS + ["ComponentType", "Symbol", "Net"],
        "Wire": ["Net", "Line", "LineWidth"],
    },
    "pcb": {
        "Component": _COMPONENT_FIELDS + ["Layer"],
        "Line": ["Net", "Layer", "StartX", "StartY", "EndX", "EndY", "LineWidth"],
        "Via": ["Net", "X", "Y", "Diameter", "HoleDiameter"],
        "Pour": ["Net", "Layer", "PourFillMethod", "LineWidth"],
    },
}
CONTEXT_QUERY = """
const project=await eda.dmt_Project.getCurrentProjectInfo();
const document=await eda.dmt_SelectControl.getCurrentDocumentInfo();
if(!project||!document)throw new Error('请打开目标工程和原理图页或PCB');
let domain=null;let selected=[];
let selectedPrimitives=[];let detailsStatus='captured';let detailsError=null;let selectedTotal=0;
if(document.documentType===1){domain='schematic';selected=await eda.sch_SelectControl.getAllSelectedPrimitives_PrimitiveId();}
else if(document.documentType===3){domain='pcb';selected=await eda.pcb_SelectControl.getAllSelectedPrimitives_PrimitiveId();}
else throw new Error('当前焦点文档不是原理图页或PCB（documentType='+String(document.documentType)+'）；请点击目标原理图页或PCB画布后重试');
if(!Array.isArray(selected)||selected.some(id=>typeof id!=='string'||!id.trim())||new Set(selected).size!==selected.length)throw new Error('所选图元ID返回格式不兼容');
try{
 const objects=domain==='schematic'?await eda.sch_SelectControl.getAllSelectedPrimitives():await eda.pcb_SelectControl.getAllSelectedPrimitives();
 if(!Array.isArray(objects))throw new Error('所选图元详情没有返回数组');
 selectedTotal=objects.length;
 const readers=__READ_FIELDS__;
 const objectIds=objects.map(primitive=>primitive.getState_PrimitiveId());
 const idsMatch=objectIds.length===selected.length&&new Set(objectIds).size===objectIds.length&&
  objectIds.every(id=>selected.includes(id));
 if(idsMatch)selectedPrimitives=objects.slice(0,__DETAIL_LIMIT__).map((primitive,index)=>{
  const type=primitive.getState_PrimitiveType();
  const typeReaders=readers[domain];
  const supported=Object.prototype.hasOwnProperty.call(typeReaders,type);
  const fields=supported?typeReaders[type]:null;
  const properties={primitive_type:type};const errors=[];const missing=[];
  for(const field of fields||[]){
   try{
    const value=primitive['getState_'+field]();
    if(value===undefined){missing.push(field);continue;}
    const serialized=JSON.stringify(value);
    if(serialized===undefined)throw new Error('属性无法序列化');
    properties[field]=JSON.parse(serialized);
   }catch(error){errors.push(field+': '+String(error).slice(0,240));}
  }
  return {primitive_id:objectIds[index],properties,read_errors:errors,
          missing_fields:missing,supported};
 });
 if(!idsMatch){detailsStatus='id_mismatch';detailsError='选中ID列表与详情对象不一致';selectedPrimitives=[];}
 else if(selectedTotal>selectedPrimitives.length)detailsStatus='truncated';
}catch(error){detailsStatus='unavailable';detailsError=String(error).slice(0,1000);selectedPrimitives=[];}
const after=await eda.dmt_SelectControl.getCurrentDocumentInfo();
const projectAfter=await eda.dmt_Project.getCurrentProjectInfo();
if(!projectAfter||project.uuid!==projectAfter.uuid||!after||after.uuid!==document.uuid||after.tabId!==document.tabId||after.documentType!==document.documentType||after.parentProjectUuid!==document.parentProjectUuid)throw new Error('读取期间工程或焦点文档变化，请重新采集');
const selectedAfter=domain==='schematic'?await eda.sch_SelectControl.getAllSelectedPrimitives_PrimitiveId():await eda.pcb_SelectControl.getAllSelectedPrimitives_PrimitiveId();
if(JSON.stringify([...selected].sort())!==JSON.stringify([...(selectedAfter||[])].sort()))throw new Error('读取期间所选图元变化，请重新采集');
return {project,document,domain,selected_ids:selected,selected_primitives:selectedPrimitives,
 selected_total:detailsStatus==='unavailable'?null:selectedTotal,
 selection_details_status:detailsStatus,selection_details_error:detailsError};
""".replace("__READ_FIELDS__", json.dumps(_READ_FIELDS)).replace(
    "__DETAIL_LIMIT__", str(MAX_SELECTED_DETAILS))


class BridgeError(RuntimeError):
    pass


class BridgeClient:
    def __init__(self, port: int, timeout: float = 10):
        if not 1 <= port <= 65535 or timeout <= 0:
            raise ValueError("bridge 端口或超时无效")
        self.base = f"http://127.0.0.1:{port}"
        self.port = port
        self.timeout = timeout
        # A local bridge must not be routed via user-configured HTTP proxies.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, route: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + route, data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                error = json.loads(exc.read(65536).decode("utf-8"))
                if isinstance(error, dict) and isinstance(error.get("error"), str):
                    detail = error["error"].strip()[:2000]
            except (OSError, ValueError):
                pass
            finally:
                exc.close()
            suffix = f"：{detail}" if detail else ""
            raise BridgeError(f"bridge {route} 请求失败（HTTP {exc.code}）{suffix}") from exc
        except (OSError, ValueError) as exc:
            raise BridgeError(f"bridge {route} 请求失败：{exc}") from exc

    def health(self) -> dict:
        result = self._request("/health")
        if not isinstance(result, dict) or result.get("service") != "easyeda-bridge":
            raise BridgeError("端口不是 EasyEDA bridge")
        return result

    def windows(self) -> list[dict]:
        result = self._request("/eda-windows")
        windows = result.get("windows") if isinstance(result, dict) else None
        if not isinstance(windows, list) or any(not isinstance(w, dict) for w in windows):
            raise BridgeError("bridge 窗口列表格式不兼容")
        return [w for w in windows if w.get("connected") is True]

    def _execute_fixed(self, code: str, window_id: str | None = None) -> tuple[dict, str]:
        self.health()
        windows = self.windows()
        ids = [w.get("windowId") for w in windows]
        if not ids:
            raise BridgeError("没有已连接的 EDA 窗口；请加载 API Gateway 并连接 bridge")
        if window_id is None:
            if len(ids) != 1:
                raise BridgeError("多个 EDA 窗口，请用 --window-id 指定：" + ", ".join(map(str, ids)))
            window_id = ids[0]
        if not isinstance(window_id, str) or not window_id or window_id not in ids:
            raise BridgeError("指定的 EDA 窗口未连接")
        reply = self._request("/execute", {"code": code, "windowId": window_id})
        if not isinstance(reply, dict) or reply.get("success") is not True:
            raise BridgeError(f"固定操作失败：{reply}")
        if reply.get("windowId") != window_id:
            raise BridgeError("bridge 返回窗口与请求不一致")
        result = reply.get("result")
        if not isinstance(result, dict):
            raise BridgeError("bridge 上下文返回格式不兼容")
        return result, window_id

    def _read_fixed(self, code: str, window_id: str | None = None) -> tuple[dict, str]:
        return self._execute_fixed(code, window_id)

    def drawing_snapshot(self, window_id: str | None = None) -> dict:
        from .drawing_api import SNAPSHOT_QUERY, validate_snapshot
        result, window_id = self._read_fixed(SNAPSHOT_QUERY, window_id)
        snapshot = {"schema_version": 1, "kind": "drawing-source", "captured_at": now(),
                    "bridge_port": self.port, "window_id": window_id, **result}
        validate_snapshot(snapshot)
        return snapshot

    def design_query(self, kind: str, window_id: str | None = None, primitive_id: str | None = None) -> dict:
        from .queries import read_query
        return read_query(self, kind, window_id, primitive_id)

    def _source_properties(self, snapshot: dict, parameters: dict) -> dict:
        from .source_properties import operation_script
        result, window = self._execute_fixed(operation_script(snapshot, parameters), snapshot["window_id"])
        return {"captured_at": now(), "window_id": window, **result}

    def drawing_components(self, window_id: str | None = None) -> dict:
        from .drawing_api import COMPONENT_QUERY
        result, window_id = self._read_fixed(COMPONENT_QUERY, window_id)
        return {"captured_at": now(), "window_id": window_id, **result}

    def drawing_svg(self, window_id: str | None = None) -> dict:
        from .drawing_api import SVG_QUERY
        result, window_id = self._read_fixed(SVG_QUERY, window_id)
        return {"captured_at": now(), "window_id": window_id, **result}

    def drawing_patch_properties(self, snapshot: dict, primitive_id: str, patch: dict) -> dict:
        from .drawing_properties import property_script, verify_property_patch
        result, window = self._execute_fixed(property_script(snapshot, primitive_id, patch), snapshot["window_id"])
        if result.get("status") == "verified":
            try:
                after = {**snapshot, "identity": result.get("identity"), "source": result.get("source")}
                result["source_verification"] = verify_property_patch(snapshot, after, primitive_id, patch)
            except ValueError as exc:
                result.update(status="uncertain", error=str(exc))
        return {"captured_at": now(), "window_id": window, **result}

    def drawing_set_attribute_visibility(self, snapshot: dict, attribute_id: str, visible: bool) -> dict:
        from .drawing_display import visibility_script, verify_visibility
        result, window = self._execute_fixed(
            visibility_script(snapshot, attribute_id, visible), snapshot["window_id"])
        if result.get("status") == "readback":
            try:
                after = {**snapshot, "identity": result["identity"], "source": result["source"]}
                result["verification"] = verify_visibility(snapshot, after, attribute_id, visible)
                result["status"] = "verified"
            except ValueError as exc:
                result.update(status="uncertain", error=str(exc))
        return {"captured_at": now(), "window_id": window, **result}

    def drawing_remove_redundant_ground_flag(self, snapshot: dict, flag_id: str, wire_id: str) -> dict:
        from .drawing_flags import remove_redundant_ground_script, verify_ground_removal
        result, window = self._execute_fixed(
            remove_redundant_ground_script(snapshot, flag_id, wire_id), snapshot["window_id"])
        if result.get("status") == "readback":
            try:
                after = {**snapshot, "identity": result["identity"], "source": result["source"]}
                result["verification"] = verify_ground_removal(snapshot, after, flag_id, wire_id)
                result["status"] = "verified"
            except ValueError as exc:
                result.update(status="uncertain", error=str(exc))
        return {"captured_at": now(), "window_id": window, **result}

    def drawing_frame_components(self, snapshot: dict, primitive_ids: list) -> dict:
        from .drawing_properties import frame_components_script
        result, window = self._execute_fixed(frame_components_script(snapshot, primitive_ids), snapshot["window_id"])
        return {"captured_at": now(), "window_id": window, **result}

    def drawing_translate(self, snapshot: dict, primitive_id: str, dx: float, dy: float) -> dict:
        from .drawing_layout import translation_script, verify_translation
        result, window = self._execute_fixed(translation_script(snapshot, primitive_id, dx, dy), snapshot['window_id'])
        if result.get('status') == 'readback':
            try:
                after = {**snapshot, 'identity': result['identity'], 'source': result['source']}
                result['verification'] = verify_translation(snapshot, after, primitive_id, dx, dy)
                result['status'] = 'verified'
            except ValueError as exc:
                result.update(status='uncertain', error=str(exc))
        return {'captured_at':now(), 'window_id':window, **result}

    def search_drawing_devices(self, keyword: str, window_id: str | None = None) -> dict:
        from .drawing_api import device_search_script
        result, window_id = self._read_fixed(device_search_script(keyword), window_id)
        return {"captured_at": now(), "window_id": window_id, "keyword": keyword, **result}

    def drawing_device_details(self, library_uuid: str, device_uuid: str, window_id: str | None = None) -> dict:
        from .drawing_api import device_details_script
        result, window_id = self._read_fixed(device_details_script(library_uuid, device_uuid), window_id)
        return {"captured_at": now(), "window_id": window_id, **result}

    def drawing_device_archive(self, library_uuid: str, device_uuid: str, window_id: str | None = None) -> dict:
        from .drawing_api import device_archive_script
        result, window_id = self._read_fixed(device_archive_script(library_uuid, device_uuid), window_id)
        return {"captured_at": now(), "window_id": window_id, **result}

    def _place_component(self, snapshot: dict, parameters: dict) -> dict:
        from .drawing_api import placement_script
        from .drawing_library import verify_mapping
        script = placement_script(snapshot, parameters)
        # Exported project provenance is checked before and after the single write.
        # The editor has no transaction lock across these read-only calls.
        try:
            verify_mapping(self.drawing_project_info(snapshot["window_id"]), snapshot, parameters)
        except (BridgeError, ValueError) as exc:
            return {"status": "blocked", "attempted": False, "error": "创建前工程来源核验失败：" + str(exc)}
        result, _ = self._execute_fixed(script, snapshot["window_id"])
        if result.get("status") == "verified":
            try:
                after = {**snapshot, "identity": result.get("identity"), "source": result.get("source")}
                record = self.drawing_project_info(snapshot["window_id"])
                # Keep the exact post-write export even if its mapping is invalid.
                result["project_evidence"] = record
                result["mapping_verified"] = verify_mapping(record, after, parameters)
            except (BridgeError, ValueError) as exc:
                result.update(status="uncertain", error="创建后工程来源核验失败：" + str(exc))
        return result

    def _circuit_operation(self, snapshot: dict, operation: dict, *, editor_version=None, inherit_net=False) -> dict:
        from .drawing_circuit import operation_script, inspect_library, library_evidence
        from .drawing_api import comparable_source
        p = operation["parameters"]
        # All live preparation is read-only. No automatic retry after /execute.
        if operation["operation"] == "place_component":
            try:
                expected = library_evidence(p)[1]
                live = self.drawing_device_archive(p["library_uuid"], p["device_uuid"], snapshot["window_id"])
                if inspect_library(live, p) != expected:
                    raise ValueError("实时库器件与方案依据不一致")
            except (ValueError, BridgeError) as exc:
                return {"status": "blocked", "attempted": False, "error": str(exc)}
        options = {} if editor_version is None else {"editor_version": editor_version}
        if inherit_net:
            options["inherit_net"] = True
        result, window = self._execute_fixed(operation_script(snapshot, operation, **options), snapshot["window_id"])
        result = {**result, "window_id": window}
        if editor_version is not None and result.get("status") == "created_pending_identity":
            from .drawing_incremental import settle_created_component
            creation = result
            try:
                result = settle_created_component(self, snapshot, operation, creation)
            except Exception as exc:
                # The write already happened. Keep its receipt/PID even if a
                # later read fails unexpectedly; never retry the creation.
                result = {**creation, "status": "uncertain", "creation_reply": creation,
                          "error": "创建后的独立身份读取失败：" + str(exc)}
        if result.get("status") == "verified" and operation["operation"] == "place_component":
            try:
                evidence = self.drawing_project_info(snapshot["window_id"])
                result["project_evidence"] = evidence
                if comparable_source(evidence["source"]) != comparable_source(result["source"]):
                    raise ValueError("创建后工程取证期间文档源变化")
            except (ValueError, BridgeError) as exc:
                result.update(status="uncertain", error=str(exc))
        return result

    def drawing_circuit_audit(self, snapshot: dict) -> dict:
        from .drawing_circuit import audit_script
        result, window = self._read_fixed(audit_script(snapshot), snapshot["window_id"])
        return {"captured_at": now(), "window_id": window, **result}

    def drawing_relocate_component(self, snapshot: dict, primitive_id: str,
                                   project_device_uuid: str, from_state: dict,
                                   to_state: dict) -> dict:
        from .drawing_circuit import relocate_component_script, verify_relocation
        result, window = self._execute_fixed(
            relocate_component_script(snapshot, primitive_id, project_device_uuid,
                                      from_state, to_state), snapshot["window_id"])
        if result.get("status") == "verified":
            try:
                result["verified_snapshot"] = verify_relocation(
                    result, snapshot, primitive_id, from_state, to_state)
            except ValueError as exc:
                result.update(status="uncertain", error="独立源回读失败：" + str(exc))
        return {"captured_at": now(), "window_id": window, **result}

    def drawing_pins(self, primitive_id: str, window_id: str | None = None) -> dict:
        from .drawing_api import component_pins_script
        result, window_id = self._read_fixed(component_pins_script(primitive_id), window_id)
        return {"captured_at": now(), "window_id": window_id, **result}

    def drawing_project_info(self, window_id: str | None = None) -> dict:
        from .drawing_api import PROJECT_LIBRARY_QUERY
        result, window_id = self._read_fixed(PROJECT_LIBRARY_QUERY, window_id)
        return {"captured_at": now(), "window_id": window_id, **result}

    def pcb_capture(self, window_id: str | None = None, *, with_drc: bool = True) -> dict:
        """Capture the complete fixed PCB read model and detailed DRC."""
        from .pcb_api import capture_script
        from .pcb import make_snapshot
        old_timeout = self.timeout
        try:
            self.timeout = max(old_timeout, 180)
            result, window = self._read_fixed(capture_script(with_drc), window_id)
        finally:
            self.timeout = old_timeout
        return make_snapshot(result, window, self.port)

    def pcb_operation(self, snapshot: dict, operation: dict) -> dict:
        """Run one fixed PCB operation; never retries a write."""
        from .pcb_api import operation_script
        from .pcb import make_snapshot
        old_timeout = self.timeout
        try:
            self.timeout = max(old_timeout, 180)
            result, window = self._execute_fixed(
                operation_script(snapshot, operation), snapshot["window_id"])
        finally:
            self.timeout = old_timeout
        if isinstance(result.get("after"), dict) and isinstance(result["after"].get("source"), str):
            try:
                result["after"] = make_snapshot(result["after"], window, self.port)
            except (ValueError, TypeError) as exc:
                result.update(status="uncertain", error="PCB写后快照格式无效：" + str(exc))
        return {"captured_at": now(), "window_id": window, **result}

    def pcb_export(self, window_id: str | None = None) -> dict:
        from .pcb_api import export_script
        result, window = self._read_fixed(export_script(), window_id)
        return {"captured_at": now(), "window_id": window, **result}

    def schematic(self, window_id: str | None = None) -> dict:
        from .schematic import SCHEMATIC_QUERY, make_snapshot
        result, window_id = self._read_fixed(SCHEMATIC_QUERY, window_id)
        return make_snapshot(result, self.port, window_id)

    def capture(self, window_id: str | None = None) -> dict:
        result, window_id = self._read_fixed(CONTEXT_QUERY, window_id)
        from .contracts import validate_context
        limitations = ["当前仅采集工程/文档元数据及所选图元；不代表完整电气连接或板级几何快照。",
                       "读取不是事务快照；焦点文档和选中集合已复核，文档内部并发编辑未被检测。"]
        if result.get("selection_details_status") != "captured":
            limitations.append("所选图元详情状态：" + str(result.get("selection_details_status")) +
                               ("；" + result["selection_details_error"] if result.get("selection_details_error") else ""))
        primitives = result.get("selected_primitives") or []
        if any(not item.get("supported") for item in primitives if isinstance(item, dict)):
            limitations.append("存在未支持类型的图元；仅记录身份和类型，未读取其属性。")
        if any(item.get("read_errors") or item.get("missing_fields")
               for item in primitives if isinstance(item, dict)):
            limitations.append("部分图元属性读取失败或缺失；详情仅代表成功读取的字段，须检查 read_errors/missing_fields。")
        limitations.append("字段保留 API 原名及原始单位；原理图按文档为 10mil，PCB 为 mil，尚未实板校准；Layer 为原始层编号。")
        limitations.append("所选图元对象详情接口为 EasyEDA API beta；应结合原始 CAD 再核对。")
        context = {"schema_version": 2, "source": "live-api", "captured_at": now(),
                   "bridge_port": self.port, "window_id": window_id,
                   **{key: result.get(key) for key in (
                       "project", "document", "domain", "selected_ids", "selected_primitives",
                       "selected_total", "selection_details_status", "selection_details_error")},
                   "limitations": limitations}
        validate_context(context)
        return context


def discover(port: int | None = None) -> BridgeClient:
    candidates = [port] if port is not None else range(49620, 49630)
    for candidate in candidates:
        client = BridgeClient(candidate, timeout=1)
        try:
            client.health()
        except BridgeError:
            continue
        client.timeout = 15
        return client
    raise BridgeError("未发现 EasyEDA bridge；请启动已安装的 API Gateway bridge。默认端口 49620–49629。")
