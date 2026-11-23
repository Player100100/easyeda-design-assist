# 非空白页已有网络支路 v5

2026-11-18，`schematic-branch-v5` 在嘉立创EDA **4.1.100** 的专用 `Agent_test` 工程完成实机验收：在两条已有测试网络上增加一个电阻支路，器件和两条导线共 **3 步通过**。独立后查确认原 **13 个完整网表器件**的属性与全部引脚连接保持，新电阻两个引脚精确进入目标网，原 **6 条详细 DRC** 逐项保持，没有新增、消失、改变或未匹配。

v5 是独立契约，[v4 独立模块新增](incremental-editing.md)的旧网名禁用和旧源不可变规则不变。旧 v2/v3 的版本限制与正式 Source 关闭状态不变。`verified` 只表示限定 CAD 编辑及观察符合契约；`engineering_verified=false`、`drc_global_freshness_established=false`，不证明电气功能或全局派生刷新。

## 使用与契约

复用 `capture-incremental-baseline`、`prepare-drawing-execution`、`approve-drawing-plan`、`execute-drawing-plan`。先归档同一窗口/工程/页/4.1.100/完整源的全部器件、引脚几何、完整网表和详细 DRC，再准备 schema v2 计划。用户明确委托自主实验时，保存真实原话和前瞻具体方案；普通绘制仍按[绘图策略](autodraw-policy.md)取得具体认可。

在 v4 的 `incremental_baseline`、`edit_region`、`reviewed_lock_sha256`、结构化操作和全部新增引脚 `connectivity` 基础上，使用以下字段：

```json
{
  "execution_contract": "schematic-branch-v5",
  "branch_anchors": [
    {"operation": "branch-a", "primitive_id": "<已有WIRE ID>", "net": "B5_OK_A", "x": 300, "y": 60},
    {"operation": "branch-b", "primitive_id": "<另一已有WIRE ID>", "net": "B5_OK_B", "x": 260, "y": 140}
  ]
}
```

计划不接受脚本、旧恢复字段或任意期望源。只支持 `place_component` 与 `add_wire`；契约上限 4 个新器件、8 个锚点，实测仅覆盖一个 FRC0603F5101TS、0°、无镜像、BOM/PCB 开启及两条支路。

每条导线必须是 A4 内的一条连通正交折线，一个端点是唯一旧线锚点，另一端是对应网的新器件引脚。操作、旧导线 ID 和网名各绑定一次；锚点须有唯一可确认的旧网名。所有新引脚进入这些明确网络。新器件须处于空闲 `edit_region`，导线可出区域但必须通过独立路径检查，避开旧本体、引脚、可见文字内部及其他旧线。不可见 Symbol 引用和空值不当作文字；文字边界仍是保守估计，不是字体渲染。

目标旧导线组的任何线段若接触其他导线组，提前拒绝，包括相同网名的重叠组。实测中这种情况会触发编辑器删除旧组和压缩后续 ticket；v5 不接受这种旧对象删除，不能只靠最终网表相同放行。

## 读回与原电路保护

固定 API 使用 `sch_PrimitiveWire.create(line)`，不重复传入旧网名，防止新增重复 NET 属性。操作参数仍保留预期 `net`，独立核验 getter、源和最终网表；传参省略不代表允许未知网名。旧 v3/v4 仍按其原规则显式传网名。

4.1.100 的 `getState_Line()` 实测为扁平的四坐标边列表，不能把整组当连续折线添加虚构的连接边。v5 解析独立边并比对完整覆盖。

编辑器在接入点可能二分旧线，并把旧 LINE ID 用于新边、把原边放到新 LINE ID。核验因此同时要求：

1. 原 WIRE ID、所有旧记录身份包络/序号及其他组完全不变，不允许删除。
2. 仅目标组的 LINE 坐标允许重新分配；样式一致，边**多重集**必须精确等于“全部旧边（至多在声明锚点处二分）＋计划新增边”，不能丢边、重复边或多边。
3. 完整几何覆盖同时精确等于旧覆盖与计划新增覆盖的并集，完整 getter 与 Source 一致。
4. 旧 NET/Relevance 只允许 `zIndex` 变化，网名、位置和其他属性均不变；其他旧记录严格保持。
5. 最终再从完整原始基线重算所有源变化，按 PID→Unique ID/Device/位号映射新增器件，核验完整旧网表对象与全部新引脚，再逐项核对详细 DRC。

每步和终审复核原历史锁清单；成功只释放本次 `incremental-active.json`。失败保留原结果、收据和活动锁，不自动重放、清理、切换接口或保存。源未逐字保持时报告 `baseline_source_preserved=false`，同时由 `baseline_source_within_contract=true` 明确表示仅发生许可变化，不伪称旧记录全部未变。

## 实机记录与失败经验

用户先后明确委托“自行设计实验并推进研发进程”“继续完成任务”，发现当前窗口为其他工程后明确要求“切回 Agent_test 后再测试”，随后确认切换完成。其他工程未写入；全部实验绑定新窗口和 `Agent_test` 原页。

| 实验 | 结果与处理 |
|---|---|
| `e1b207…` 原 IN/RET 支路 | 首条线新增了重复 NET，并在锚点分段；旧核验拒绝。独立核对完整前缀后恢复初始源、网表和 DRC，三次一致。|
| `33becf…` 修正版 | 创建后工程库归档遇到一次 Network Error；创建收据及稳定身份保留，没有重建。独立取证确认唯一新器件全浮空后清理，完整基线恢复。|
| `0eb3a5…` 原 RET 接入 | 实测发现原页 RET 存在局部重叠分组；第三次调用删除一组、合并到另一组并新增重复网名。旧器件网表保持，但严格失败。精确复核六条删除、ticket 压缩、完整覆盖与 DRC 后单次归档恢复；此行为现已在预检阻断。|
| `ef0fcd…` 临时双电阻模块 | v4 六步通过，建立无重叠目标组。|
| `67ae69…` 临时模块接入 | 编辑器在组内重分配 LINE ID，旧核验再次拒绝。新增精确边多重集检查。恢复时新建测试器件的 Device 引用被编辑器物化为空；网表仍相同，但完整源验收失败，未解除活动锁。独立确认所有前模块基线记录仍精确保留后，清理整个临时模块并恢复到建模块前稳定基线，三次完整一致。|
| `e56465…` 最终测试模块 | 从稳定基线以新网名 `B5_OK_A/B` 建立两个并联电阻、两个标号、两组导线，v4 六步通过。|
| `fb2dd8…` 最终支路 | 新电阻与两组物理支路三步通过；独立后查再核对全部旧器件、完整源许可差异、两新引脚及详细 DRC，通过。|

独立实验调查入口 `drawing_branch_experiment.py` 不进入正常调度。它只接受完整归档的已观察失败前缀，不接受任意期望源。已完成的重叠组合并一次性调查实现归档到外部 `physical-branch/investigation/consolidation_cleanup.py`，不保留在正常源码入口。清理目标只能来自归档输入；一次写后连续三次完整源、网表、DRC 一致才释放锁。恢复不完整的中间结果保留，不覆盖为成功；最后的跨实验测试模块清理单独归档。

最终布局：R4 `(200,100)`、R5 `(400,100)` 为先建测试电阻，R6 `(850,100)` 为 v5 新增电阻。R6 引脚 1 `(830,100)` 接 `B5_OK_A`，引脚 2 `(870,100)` 接 `B5_OK_B`。旧线内部接点分别 `(300,60)`、`(260,140)`。单位为 API 10mil。

首轮实测 SVG 显示部分路径经过图框标题栏。该结果继续保留为对照样本。

## 图框避让复测

v4 和 v5 的有框基线包含与同一窗口、工程、页及完整源绑定的原生 SVG。工具读取内框、标题栏单元格与图框标志，根据包围矩形向外取整后预留 5 个 API 坐标单位。预检展示 `sheet_frame`，检查编辑区域及逐条支路线段。执行前重新导出 SVG 并核对几何。有图框的旧基线可读用于审计，执行新方案需重新采集。当前核验范围为 4.1.100、原点未偏移、标题栏位置编号为 3 的 A4 图框。其他图框布局会被拒绝，文字字形范围未计算。

在 Agent_test 同一实时基线验证了标题栏内的编辑区、穿过标题栏的支路和净距不足的线路，三个反例都在写入前被拒绝。合规方案在 `(80, 200)` 新增电阻 R7，两条支路从标题栏外侧接入 `B5_OK_A` 与 `B5_OK_B`。3 步实机执行通过，独立导出的 SVG 复核了全部 7 段新增导线，原 14 个完整网表器件和 6 条详细 DRC 警告保持。旧测试走线仍在当前实验页。

[复测方案](../../easyeda-设计助手-data/branch-experiments/20261118/frame-clearance/native-svg/experiment-plan.json) · [独立验收结果](../../easyeda-设计助手-data/branch-experiments/20261118/frame-clearance/native-svg/completion-result.json) · [实机 SVG](../../easyeda-设计助手-data/branch-experiments/20261118/frame-clearance/native-svg/after.svg)

## 证据

- 图框避让复测后，完整自动测试 **279 项通过**。首轮 v5 的测试与文件摘要仍见[首轮实现验收记录](../../easyeda-设计助手-data/branch-experiments/20261118/physical-branch/acceptance-branch/implementation-verification.json)。
- [最终前瞻方案](../../easyeda-设计助手-data/branch-experiments/20261118/physical-branch/acceptance-branch/experiment-plan.json)、[独立后查结论](../../easyeda-设计助手-data/branch-experiments/20261118/physical-branch/acceptance-branch/completion-result.json)。
- [最终执行](../../easyeda-设计助手-data/drawing-executions/fb2dd8b8c682908e5275291db5430ee1b17668ffb7c004fdd8056afdfe0d3527/result.json)、[终审](../../easyeda-设计助手-data/drawing-executions/fb2dd8b8c682908e5275291db5430ee1b17668ffb7c004fdd8056afdfe0d3527/final-audit.json)、[CAD SVG](../../easyeda-设计助手-data/branch-experiments/20261118/physical-branch/acceptance-branch/after-svg.svg)。
- [临时模块清理与完整稳定恢复](../../easyeda-设计助手-data/drawing-executions/67ae6902d2627d0d89444581167e32772345a585172d35b9723cb78359d6df85/fixture-baseline-cleanup/explicit-baseline-source-restore/result.json)。

最终独立源摘要 `c1efc9b823a6913c68f2b1b5607cf24bec032ffa29fbb57703b966243c62f363`；四份历史锁清单摘要仍为 `5268c61e2c7abcdf1b1ed36e3b38d9394bb59e078fc4892d57d4e651b8918167`，无活动锁。实验模块保留在当前页，助手未调用保存或重开。证据位于外部数据目录，不随源码克隆分发。
