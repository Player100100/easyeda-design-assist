"""Static operation catalogue shared by policy, preview and execution.

API discovery cannot register operations or enable writes. Legacy adapters keep
their existing scope; Source stays closed after a live trial found a side effect.
"""
from __future__ import annotations

from copy import deepcopy

V2 = "schematic-place-component-v2"
V3 = "schematic-circuit-v3"
SOURCE = "schematic-source-properties-v1"
INCREMENTAL = "schematic-incremental-v4"
BRANCH = "schematic-branch-v5"
PCB = "pcb-drawing-v1"
LEGACY_VERSION = "3.2.149.88089769"
SOURCE_VERSION = "4.1.100"
CATALOGUE = {
    "place_component": {"impact": "component-and-pins", "adapters": [V2, V3, INCREMENTAL, BRANCH]},
    "place_net_flag": {"impact": "connectivity", "adapters": [V3, INCREMENTAL]},
    "add_wire": {"impact": "connected-component", "adapters": [V3, INCREMENTAL, BRANCH]},
    "set_property": {"impact": "existing-attribute-values", "adapters": [SOURCE]},
    "move_component": {"impact": "geometry-and-connectivity", "adapters": []},
    "rotate_component": {"impact": "geometry-and-connectivity", "adapters": []},
    "edit_wire": {"impact": "connected-component", "adapters": []},
    "place_graphic": {"impact": "graphics", "adapters": []},
    "move_pcb_component": {"impact": "pcb-component-geometry", "adapters": [PCB]},
    "add_pcb_line": {"impact": "pcb-routing", "adapters": [PCB]},
    "add_pcb_via": {"impact": "pcb-routing", "adapters": [PCB]},
    "add_pcb_outline": {"impact": "pcb-mechanical-boundary", "adapters": [PCB]},
    "add_pcb_pour": {"impact": "pcb-copper", "adapters": [PCB]},
    "rebuild_pcb_pour": {"impact": "pcb-copper", "adapters": [PCB]},
    "route_pcb_nets": {"impact": "pcb-routing", "adapters": [PCB]},
    "auto_place_pcb": {"impact": "pcb-component-geometry", "adapters": [PCB]},
}
ADAPTERS = {
    BRANCH: {"editor_version": SOURCE_VERSION, "dispatch_enabled": True,
             "write_validation": "live-two-existing-net-physical-branch", "evidence": "docs/branch-editing.md",
             "limitations": ["非空白页新增器件与物理支路；每条导线仅接触一个明确绑定的旧导线锚点。",
                             "旧导线覆盖保持；目标组内允许LINE几何重分配和锚点二分，边多重集、包络、样式须精确匹配，无删除。",
                             "完整旧网表和详细DRC保持；逐步核验与最终网表不等于工程功能验收。",
                             "4.1.100实测一个0度电阻接入两个旧网络，3步通过；重叠或接触其他导线组的目标预检拒绝。",
                             "预检检查已校准 A4 图框内边界及标题栏保留区。未知图框布局会被拒绝。",
                             "不移动、删除或重接旧器件；其他参数未实测，正式Source保持关闭。"]},
    INCREMENTAL: {"editor_version": SOURCE_VERSION, "dispatch_enabled": True,
                  "write_validation": "live-nonempty-three-resistor-loop", "evidence": "docs/incremental-editing.md",
                  "limitations": ["非空白页空闲区域新增独立电路；4.1.100三电阻闭环、3标号、3组导线共9步已实测。",
                                  "严格保留原图元与完整网表；不删除、移动或重接原电路，不复用旧网名。",
                                  "详细DRC不得新增、消失、改变或未匹配；既有警告原样保留，不表示全页DRC清零。",
                                  "编辑区避让已校准的 A4 图框和标题栏。未知图框布局会被拒绝。",
                                  "本次仅覆盖0度、无镜像、BOM/PCB开启；其他参数及工程功能尚未实测。"]},
    V2: {"editor_version": LEGACY_VERSION, "dispatch_enabled": True,
         "write_validation": "single-placement-only", "evidence": "docs/live-api-verification.md",
         "limitations": ["单器件、请求90°、无镜像、BOM/PCB关闭实测；其余组合未覆盖。"]},
    V3: {"editor_version": LEGACY_VERSION, "dispatch_enabled": True,
         "write_validation": "experimental-existing-contract", "evidence": "docs/circuit-executor.md",
         "limitations": ["保留已有实验契约，通用电路端到端实机验收尚未完成。"]},
    SOURCE: {"editor_version": SOURCE_VERSION, "dispatch_enabled": False,
             "write_validation": "live-name-roundtrip-strict-failed", "evidence": "docs/source-validation.md",
             "limitations": ["4.1.100的U2 Name单例实测发现ATTR zIndex从16变为15，正向严格源验收失败。",
                             "历史zIndex残留已由独立单字段修复恢复；Source.update本身的副作用仍未修复，正式调度保持关闭。",
                             "仅独立单次实验路径可按明确测试授权运行；不证明其他属性、版本或全局刷新兼容。",
                             "契约仅覆盖打开页、已有白名单属性值，不允许身份、几何或连接变化。"]},
    PCB: {"editor_version": SOURCE_VERSION, "dispatch_enabled": False,
          "write_validation": "live-limited-pcb-toolchain",
          "evidence": "docs/pcb-live-verification.md",
          "limitations": ["4.1.100 Agent_test实测17器件/40焊盘读取与映射、两电阻移动旋转、顶底走线、12/24mil通孔、矩形板框、铜区重建、单网络自动布线及制造导出。",
                          "铜区自定义线宽/优先级未兑现；只允许每层首铜区的已知默认参数，复杂参数保持实验边界。原生整板布局不带动既有铜，已布线/铺铜板预检阻断。",
                          "PCB独立执行入口需明确方案认可，并在指定测试PCB上显式使用experimental-pcb；普通原理图执行器不调度此适配器。",
                          "自动布局/布线结果是候选，不能替代功率、热、阻抗、SI/PI、EMC或实板验证。"]},
}
POLICY_OPERATIONS = frozenset(CATALOGUE)
# Keep the old public summary stable; capabilities() carries adapter detail.
LEGACY_OPERATIONS = ("place_component", "add_wire", "place_net_flag")


def adapter_for(plan):
    name = plan.get("execution_contract")
    if name is None:
        name = V2
    if not isinstance(name, str) or name not in ADAPTERS:
        raise ValueError("未知执行契约")
    return name


def operation_names(adapter):
    if adapter not in ADAPTERS:
        raise ValueError("未知执行契约")
    return {name for name, spec in CATALOGUE.items() if adapter in spec["adapters"]}


def require_dispatch(adapter):
    if not ADAPTERS[adapter]["dispatch_enabled"]:
        raise ValueError("当前适配器尚未完成真实 CAD 写入验证，禁止执行：" + adapter)


def validate_parameters(adapter, operation):
    if operation.get("operation") not in operation_names(adapter):
        raise ValueError("尚未实现的写入操作：" + str(operation.get("operation")))
    if adapter == V2:
        from .drawing_api import validate_parameters as validate
        validate(operation["parameters"])
    elif adapter in {V3, INCREMENTAL, BRANCH}:
        from .drawing_circuit import validate_operation
        validate_operation(operation)
    else:
        from .source_properties import validate_parameters as validate
        validate(operation["parameters"])


def execute_once(adapter, bridge, snapshot, operation):
    if adapter == V2:
        return bridge._place_component(snapshot, operation["parameters"])
    if adapter == V3:
        return bridge._circuit_operation(snapshot, operation)
    if adapter == BRANCH:
        return bridge._circuit_operation(snapshot, operation, editor_version=SOURCE_VERSION,
                                         **({"inherit_net": True} if operation["operation"] == "add_wire" else {}))
    if adapter == INCREMENTAL:
        return bridge._circuit_operation(snapshot, operation, editor_version=SOURCE_VERSION)
    require_dispatch(adapter)
    return bridge._source_properties(snapshot, operation["parameters"])


def capabilities(editor_version=None):
    return {"schema_version": 1, "kind": "drawing-capabilities", "editor_version": editor_version,
            "operations": [{"operation": name, "impact": spec["impact"], "implementations": [
                {"adapter": adapter, **deepcopy(ADAPTERS[adapter]),
                 "version_matches": None if editor_version is None else editor_version == ADAPTERS[adapter]["editor_version"],
                 "available": ADAPTERS[adapter]["dispatch_enabled"] and editor_version == ADAPTERS[adapter]["editor_version"]}
                for adapter in spec["adapters"]]} for name, spec in CATALOGUE.items()],
            "experimental_property_backends": [
                {"backend": "canvas-attribute", "editor_version": SOURCE_VERSION,
                 "entry_point": "execute-source-trial --backend canvas-attribute",
                 "normal_dispatch_enabled": False, "write_validation": "live-name-value-roundtrips",
                 "evidence": "docs/source-experiment.md",
                 "limitations": ["仅固定单次实验；U2 Name、C3 Value在4.1.100实测保留层级，已完成恢复。",
                                 "DRC响应须独立逐项核对；不代表全部属性、版本、网络操作或全局刷新兼容。"]}],
            "limitations": ["available 表示既有契约和版本允许调度，不代表全部参数已实测或电气设计正确。",
                            "实际执行再次检查目标、完整源、版本、具体方案及认可；不会自动重试写入。"]}
